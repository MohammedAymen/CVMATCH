import os, sys, requests
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from dotenv import load_dotenv
load_dotenv()

api_key = os.getenv("GEMINI_API_KEY")
model = "gemini-2.5-flash"

TEST_PROMPT = """You are an expert recruiter. Evaluate this job match.

## Job:
Title: Python Backend Engineer
Description: Building REST APIs with FastAPI and PostgreSQL
Requirements: Python, FastAPI, PostgreSQL, Docker, 2+ years experience

## Candidate Profile:
[Source: CV]
Python developer with 3 years experience. Built REST APIs using FastAPI and Flask.
Experience with PostgreSQL and Redis. Personal projects on GitHub.

## Return ONLY this JSON (no other text):
{
  "score": 75,
  "confidence": "Medium",
  "strengths": ["Strong Python", "FastAPI experience"],
  "gaps": ["No Docker evidence"],
  "recommendations": ["Add Docker project"],
  "summary": "Good match for backend role"
}"""

for model_name in ["gemini-3.5-flash", "gemini-2.0-flash", "gemini-2.5-flash-lite"]:
    print(f"\n{'─'*50}")
    print(f"Testing: {model_name}")
    
    # بدون responseMimeType
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
    
    for use_mime in [False, True]:
        config = {"temperature": 0.15, "maxOutputTokens": 1200, "topP": 0.9}
        if use_mime:
            config["responseMimeType"] = "application/json"
        
        payload = {
            "contents": [{"parts": [{"text": TEST_PROMPT}]}],
            "generationConfig": config,
        }
        
        try:
            resp = requests.post(url, json=payload, timeout=30)
            if resp.status_code != 200:
                print(f"  [mime={use_mime}] ❌ Status {resp.status_code}: {resp.text[:100]}")
                continue
            
            data = resp.json()
            candidates = data.get("candidates", [])
            if not candidates:
                print(f"  [mime={use_mime}] ❌ No candidates")
                continue
            
            parts = candidates[0].get("content", {}).get("parts", [])
            print(f"  [mime={use_mime}] Parts count: {len(parts)}")
            for i, p in enumerate(parts):
                has_thought = bool(p.get("thoughtSignature"))
                text = p.get("text", "")
                print(f"    Part {i}: thoughtSignature={has_thought} | text_len={len(text)} | preview: {text[:80]!r}")
        except Exception as e:
            print(f"  [mime={use_mime}] ❌ Error: {e}")