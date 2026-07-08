"""
debug_gemini.py — بيجيب كل الـ models المتاحة عندك
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from dotenv import load_dotenv
load_dotenv()

import requests

api_key = os.getenv("GEMINI_API_KEY", "")
print(f"🔑 Key prefix: {api_key[:15]}...")

# جيب كل الـ models المتاحة
print("\n📋 Fetching available models...")
url = f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}"
resp = requests.get(url, timeout=15)
print(f"Status: {resp.status_code}")

if resp.status_code == 200:
    models = resp.json().get("models", [])
    # فلتر بس اللي بيدعم generateContent
    generate_models = [
        m["name"].replace("models/", "")
        for m in models
        if "generateContent" in m.get("supportedGenerationMethods", [])
    ]
    print(f"\n✅ Models supporting generateContent ({len(generate_models)}):")
    for m in sorted(generate_models):
        print(f"   - {m}")
else:
    print(f"❌ Error: {resp.text[:300]}")