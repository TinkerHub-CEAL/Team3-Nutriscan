# Team3-Nutriscan
# 🥗 NUTRISCAN – Smart Food Ingredient Assessment System

NUTRISCAN is a real-time food ingredient awareness web application that helps users make informed purchasing decisions based on dietary restrictions and health goals.

It acts as an intelligent middleware engine between consumers and the Open Food Facts database, analyzing ingredients and nutritional data to provide instant, easy-to-understand feedback.

---

## 🚀 Features

### 🔍 Barcode-Based Product Lookup
Fetches product data using a barcode from the Open Food Facts API.

### 🚨 Strict Dietary Enforcement
Scans ingredient lists for high-risk keywords to protect:

- Halal users
- Vegan users

Detects ingredients such as:
- Gelatin
- Lard
- Pork
- Alcohol
- Whey
- Casein
- Milk
- Egg

### 📊 Health Meter (0–100 Score)
Custom scoring algorithm that:

- Rewards high protein & fiber
- Penalizes high sugar, sodium & saturated fat
- Returns a color-coded result (Green / Orange / Red)

### 📦 Clean & Frontend-Ready JSON
Transforms raw API data into structured, simplified JSON for instant UI feedback.

---

## 🏗 Tech Stack

**Backend**
- Python
- Flask
- Requests

**API Source**
- Open Food Facts

---

## 📂 Project Structure

url:team3-nutriscan-production.up.railway.app
