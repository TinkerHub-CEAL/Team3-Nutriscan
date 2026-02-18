"""
NutriScan Backend — Flask API
=============================
Provides endpoints for scanning barcodes and searching products
using the OpenFoodFacts public API.

Endpoints:
  POST /scan-barcode   → Fetch product by barcode
  POST /search-product → Search product by name (returns first match)
"""

import os
import re
import requests
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from dotenv import load_dotenv

import csv

# ──────────────────────────────────────────────
# 1. Configuration
# ──────────────────────────────────────────────
load_dotenv()  # Load environment variables from .env file

# Frontend folder is right next to this file
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FRONTEND_DIR = os.path.join(BASE_DIR, "Frontend")
ADDITIVES_CSV_PATH = os.path.join(BASE_DIR, "additives.csv")

app = Flask(__name__, static_folder=FRONTEND_DIR, static_url_path="/static")
CORS(app)  # Enable CORS so the frontend can call this API

# Base URLs
PRODUCT_API_URL = "https://world.openfoodfacts.org/api/v0/product/{barcode}.json"
SEARCH_API_URL  = "https://world.openfoodfacts.org/cgi/search.pl"
UPCITEMDB_URL   = "https://api.upcitemdb.com/prod/trial/lookup?upc={barcode}"

API_KEY = os.getenv("OPENFOODFACTS_API_KEY", "")

# ──────────────────────────────────────────────
# 2. Database — Additives (loaded from CSV)
# ──────────────────────────────────────────────
ADDITIVES_DB = {}

def load_additives_db():
    """
    Load data from additives.csv into a dictionary for fast lookup.
    Key: e_code (normalized to lowercase, e.g. 'e100')
    Value: dict with fields (title, info, e_type, halal_status)
    """
    global ADDITIVES_DB
    if not os.path.exists(ADDITIVES_CSV_PATH):
        print(f"⚠️ Warning: additives.csv not found at {ADDITIVES_CSV_PATH}")
        return

    try:
        with open(ADDITIVES_CSV_PATH, mode="r", encoding="utf-8", errors="replace") as f:
            reader = csv.DictReader(f)
            for row in reader:
                code_raw = row.get("e_code", "").strip()
                if not code_raw:
                    continue
                
                # Normalize key: 'E100' -> 'e100'
                key = code_raw.lower()
                ADDITIVES_DB[key] = {
                    "code": code_raw,
                    "title": row.get("title", "").strip(),
                    "info": row.get("info", "").strip(),
                    "type": row.get("e_type", "").strip(),
                    "status": row.get("halal_status", "").strip()
                }
        print(f"✅ Loaded {len(ADDITIVES_DB)} additives from CSV.")
    except Exception as e:
        print(f"❌ Error loading additives CSV: {e}")

# Load immediately on startup
load_additives_db()


# ──────────────────────────────────────────────
# 3. Helper — Clean & Parse Ingredients Text
# ──────────────────────────────────────────────
def parse_ingredients(raw_text):
    """
    Convert a raw ingredients string into a clean list.
    """
    if not raw_text:
        return []
    parts = re.split(r"[,;•]", raw_text)
    cleaned = [item.strip() for item in parts if item.strip()]
    return cleaned


# ──────────────────────────────────────────────
# 4. Helper — Enrich Additives from DB
# ──────────────────────────────────────────────
def enrich_additives(additive_tags):
    """
    Given a list of additive tags (e.g. ['en:e330', 'en:e211']),
    return a list of full objects with details from the CSV.
    """
    enriched_data = []

    if not additive_tags:
        return enriched_data

    for tag in additive_tags:
        # Clean the tag: remove language prefix like "en:" -> "e330"
        clean_tag = tag.replace("en:", "").strip().lower()
        
        # Lookup in DB
        info = ADDITIVES_DB.get(clean_tag)
        
        if info:
            enriched_data.append(info)
        else:
            # Fallback if not found in CSV
            enriched_data.append({
                "code": clean_tag.upper(),
                "title": clean_tag.upper(),
                "info": "No detailed information available.",
                "type": "Unknown",
                "status": "Unknown"
            })

    return enriched_data

# ──────────────────────────────────────────────
# 5. Helper — Infer Allergens from Ingredients
# ──────────────────────────────────────────────
COMMON_ALLERGENS = {
    "milk": ["milk", "cream", "whey", "casein", "lactose", "curd", "cheese", "butter", "yogurt"],
    "egg": ["egg", "albumin", "yolk", "mayonnaise"],
    "peanut": ["peanut", "groundnut"],
    "nut": ["nut", "almond", "cashew", "walnut", "pecan", "hazelnut", "pistachio", "macadamia"],
    "soy": ["soy", "soya", "tofu", "bean curd", "lecithin"],
    "fish": ["fish", "salmon", "tuna", "cod", "anchovy"],
    "shellfish": ["shellfish", "shrimp", "prawn", "crab", "lobster", "clam", "mussel", "oyster"],
    "wheat": ["wheat", "gluten", "barley", "rye", "oats", "flour", "bread", "pasta"],
    "sesame": ["sesame", "tahini"],
    "mustard": ["mustard"],
    "celery": ["celery"],
    "sulfites": ["sulfite", "sulphite", "sulfur", "sulphur", "metabisulfite", "dioxide"]
}

def check_allergens_in_ingredients(ingredients_list):
    """
    Scan the ingredients list for common allergen keywords.
    Returns a list of potential allergens found (e.g. ['Milk', 'Soy']).
    """
    found = set()
    # Join list to text for easier searching, or check each item
    text = " ".join(ingredients_list).lower()
    
    for allergen, keywords in COMMON_ALLERGENS.items():
        for kw in keywords:
            # Check for keyword as a whole word or significant part
            if kw in text:
                found.add(allergen.capitalize())
                break
    return list(found)# ──────────────────────────────────────────────
# 4. Helper — Build a Structured Response
# ──────────────────────────────────────────────
def build_product_response(product):
    """
    Extract and structure only the required fields from an
    OpenFoodFacts product object.
    Removes null / empty values automatically.
    """
    # Extract raw fields - Prioritize English if available
    product_name   = product.get("product_name", product.get("product_name_en", ""))
    image_url      = product.get("image_url", "")
    
    # Try multiple ingredient sources for English
    ingredients_raw = product.get("ingredients_text_en")
    if not ingredients_raw:
        # Fallback: Extract English names from the structured ingredient objects if available
        # OFF often has lists of ingredients with 'text' and 'id' (like 'en:sugar')
        ings = product.get("ingredients", [])
        if ings:
            # Prefer 'text' from objects that have it, otherwise clean the ID
            en_names = []
            for i in ings:
                txt = i.get("text")
                if not txt:
                    # Clean up 'en:sugar' -> 'Sugar'
                    txt = i.get("id", "").split(":")[-1].replace("-", " ").capitalize()
                if txt: en_names.append(txt)
            
            if en_names:
                ingredients_raw = ", ".join(en_names)
    
    if not ingredients_raw:
        # Last resort: generic text field
        ingredients_raw = product.get("ingredients_text", "")
    
    additive_tags  = product.get("additives_tags", [])

    # ---- Additional fields the frontend needs ----
    categories     = product.get("categories", "")
    nutriscore     = product.get("nutriscore_score")
    allergens_tags = product.get("allergens_tags", [])
    ingredients_analysis = product.get("ingredients_analysis_tags", [])

    # Parse ingredients text into a clean list
    ingredients = parse_ingredients(ingredients_raw)

    # Enrich additives with CSV data
    enriched_additives = enrich_additives(additive_tags)

    # Build response, omitting empty/null values
    response = {}

    if product_name:
        response["product_name"] = product_name
    if image_url:
        response["image"] = image_url
    if ingredients:
        response["ingredients"] = ingredients
    if enriched_additives:
        response["additives"] = enriched_additives

    # Extra fields for frontend compatibility
    if categories:
        response["categories"] = categories
    if nutriscore is not None:
        response["nutriscore_score"] = nutriscore
    if allergens_tags:
        response["allergens_tags"] = [a.replace("en:", "") for a in allergens_tags]
    elif ingredients:
        # Fallback: Infer from ingredients if API has no tags
        inferred = check_allergens_in_ingredients(ingredients)
        if inferred:
            response["allergens_tags"] = [f"May contain: {a}" for a in inferred]
            
    if ingredients_analysis:
        response["ingredients_analysis_tags"] = ingredients_analysis

    return response


# ──────────────────────────────────────────────
# 6. Fallback Helpers (UPCitemdb & Search)
# ──────────────────────────────────────────────
def fetch_upcitemdb(barcode):
    """
    Fallback to UPCitemdb to get product name/image if OFF fails.
    """
    try:
        resp = requests.get(UPCITEMDB_URL.format(barcode=barcode), timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            if data.get("items"):
                item = data["items"][0]
                return {
                    "product_name": item.get("title", ""),
                    "image_url": item.get("images", [""])[0] if item.get("images") else "",
                }
    except Exception as e:
        print(f"⚠️ UPCitemdb fallback failed: {e}")
    return None

def search_products_list(name, limit=20):
    """
    Search OFF and return a list of simplified product objects.
    Used for the manual Search feature so user can choose.
    """
    try:
        resp = requests.get(
            SEARCH_API_URL,
            params={"search_terms": name, "search_simple": 1, "json": 1, "page_size": limit},
            timeout=8
        )
        if resp.status_code == 200:
            data = resp.json()
            products = data.get("products", [])
            
            # Format results for frontend
            results = []
            for p in products:
                # Essential fields only
                results.append({
                    "barcode": p.get("code") or p.get("id", ""),
                    "product_name": p.get("product_name", "Unknown Product"),
                    "brand": p.get("brands", ""),
                    "image": p.get("image_url", "") or p.get("image_small_url", ""),
                    "categories": p.get("categories", "")
                })
            return results
    except Exception as e:
        print(f"⚠️ OFF Search list failed: {e}")
    return []

def find_best_match_by_name(name):
    """
    Search OpenFoodFacts by name and auto-select the best match (with ingredients).
    Used for the Scan Fallback logic.
    """
    try:
        resp = requests.get(
            SEARCH_API_URL,
            params={"search_terms": name, "search_simple": 1, "json": 1, "page_size": 10},
            timeout=8
        )
        if resp.status_code == 200:
            data = resp.json()
            products = data.get("products", [])
            
            if not products:
                return None

            # Strategy: Look for the best quality data
            # 1. Any product with explicit ingredients text
            for p in products:
                if p.get("ingredients_text"):
                    return p
            
            # 2. Fallback: Just take the first result
            return products[0]

    except Exception as e:
        print(f"⚠️ OFF Best Match fallback failed: {e}")
    return None


# ──────────────────────────────────────────────
# 5. Endpoint — POST /scan-barcode
# ──────────────────────────────────────────────
@app.route("/scan-barcode", methods=["POST"])
def scan_barcode():
    """
    Accepts JSON: { "barcode": "1234567890123" }
    Returns structured product data or an error.
    """

    # --- 5a. Validate request body ---
    data = request.get_json(silent=True)
    if not data or "barcode" not in data:
        return jsonify({"error": "Missing 'barcode' field in request body"}), 400

    barcode = str(data["barcode"]).strip()
    if not barcode:
        return jsonify({"error": "Barcode cannot be empty"}), 400

    # Basic barcode format check (digits only, 8-13 chars typical)
    if not re.match(r"^\d{4,14}$", barcode):
        return jsonify({"error": "Invalid barcode format. Must be 4-14 digits."}), 400

    # --- 5b. Call external API (OpenFoodFacts) ---
    product = {} 
    found_in_off = False
    
    try:
        url = PRODUCT_API_URL.format(barcode=barcode)
        headers = {}
        if API_KEY:
            headers["Authorization"] = f"Bearer {API_KEY}"

        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code == 200:
            api_data = resp.json()
            if api_data.get("status") == 1:
                product = api_data.get("product", {})
                found_in_off = True

    except Exception as e:
        print(f"⚠️ Main API Error: {e}")

    # --- 5c. Fallback 1: UPCitemdb (if not found in OFF) ---
    if not found_in_off:
        print("Product not found in OFF, trying UPCitemdb...")
        upc_data = fetch_upcitemdb(barcode)
        if upc_data:
            product.update(upc_data) # Use name/image from UPCitemdb
    
    # --- 5d. Fallback 2: Search OFF by Name (if ingredients missing) ---
    # Case: We have a Name (from OFF or UPCitemdb) but NO ingredients.
    # We search OFF by name to find a sibling product with data.
    if product.get("product_name") and not product.get("ingredients_text"):
        print(f"Missing ingredients for '{product['product_name']}', searching by name...")
        sibling = find_best_match_by_name(product["product_name"])
        if sibling:
            # Merge fields if missing in original
            # We prioritize the sibling's data for ingredients/nutriscore
            if not product.get("ingredients_text"):
                product["ingredients_text"] = sibling.get("ingredients_text", "")
            if not product.get("additives_tags"):
                product["additives_tags"] = sibling.get("additives_tags", [])
            if not product.get("allergens_tags"):
                product["allergens_tags"] = sibling.get("allergens_tags", [])
            if product.get("nutriscore_score") is None:
                product["nutriscore_score"] = sibling.get("nutriscore_score")
            # If original had no image, take sibling's
            if not product.get("image_url"):
                product["image_url"] = sibling.get("image_url", "")

    # --- 5e. Build Response ---
    if not product.get("product_name"):
         return jsonify({"error": "Product not found in any database"}), 404

    result = build_product_response(product)

    if not result:
        return jsonify({"error": "Product found but contains no usable data"}), 404

    return jsonify(result), 200


# ──────────────────────────────────────────────
# 6. Endpoint — POST /search-product
# ──────────────────────────────────────────────
@app.route("/search-product", methods=["POST"])
def search_product():
    """
    Accepts JSON: { "name": "chocolate" }
    Searches OpenFoodFacts by name, returns the first matching product.
    """

    data = request.get_json(silent=True)
    if not data or "name" not in data:
        return jsonify({"error": "Missing 'name' field in request body"}), 400

    name = str(data["name"]).strip()
    if not name:
        return jsonify({"error": "Product name cannot be empty"}), 400

    # --- Call search API (return list) ---
    results = search_products_list(name)

    if not results:
        return jsonify({"error": "No products found matching that name"}), 404

    return jsonify({"products": results, "count": len(results)}), 200


# ──────────────────────────────────────────────
# 7. Serve Frontend & Health Check
# ──────────────────────────────────────────────
@app.route("/", methods=["GET"])
def serve_frontend():
    """Serve the main frontend HTML page."""
    return send_from_directory(FRONTEND_DIR, "index3nutripro.html")


@app.route("/health", methods=["GET"])
def health():
    """Simple health-check endpoint."""
    return jsonify({"status": "ok", "service": "NutriScan API"}), 200


# ──────────────────────────────────────────────
# 8. Run the server
# ──────────────────────────────────────────────
if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    debug = os.getenv("FLASK_DEBUG", "true").lower() == "true"
    print(f"\n🚀 NutriScan API running on http://localhost:{port}\n")
    app.run(host="0.0.0.0", port=port, debug=debug)
