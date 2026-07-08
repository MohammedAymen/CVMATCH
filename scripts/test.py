"""
scripts/test_ai_client.py
"""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# ✅ لازم يتعمل قبل أي import تاني
from dotenv import load_dotenv
load_dotenv()  # بيقرأ .env من المجلد الحالي

from core.ai_client import AIClient, LLMProvider

TEST_PROMPT = 'Reply with ONLY this JSON, nothing else: {"status": "ok"}'


def test_single_provider(provider: LLMProvider):
    print(f"\n{'─'*45}")
    print(f"Testing {provider.value.upper()}...")

    import time
    client = AIClient()

    for p in LLMProvider:
        if p != provider:
            client._states[p].disabled_until = time.time() + 9999

    try:
        resp = client.complete(TEST_PROMPT, max_tokens=50)
        print(f"  ✅ Response: {resp.text[:120]}")
        print(f"  📍 Provider: {resp.provider_used.value}")
        return True
    except Exception as e:
        print(f"  ❌ Failed: {e}")
        return False


def test_full_chain():
    print(f"\n{'─'*45}")
    print("Testing FULL CHAIN...")
    client = AIClient()
    try:
        resp = client.complete(TEST_PROMPT, max_tokens=50)
        print(f"  ✅ Response: {resp.text[:120]}")
        print(f"  📍 Won by: {resp.provider_used.value}")
        print(f"\n  📊 Stats:")
        for provider, stats in client.get_stats().items():
            status = "✅" if stats["available"] else "⛔"
            print(f"     {status} {provider:8s}: calls={stats['total_calls']} | failures={stats['total_failures']}")
    except Exception as e:
        print(f"  ❌ Chain failed: {e}")


def show_config():
    print("\n🔧 Environment Check:")
    groq_key = os.getenv("GROQ_API_KEY", "")
    gemini_key = os.getenv("GEMINI_API_KEY", "")
    print(f"   GROQ_API_KEY   : {'✅ ' + groq_key[:8] + '...' if groq_key else '❌ NOT FOUND'}")
    print(f"   GEMINI_API_KEY : {'✅ ' + gemini_key[:8] + '...' if gemini_key else '❌ NOT FOUND'}")
    print(f"   OLLAMA_URL     : {os.getenv('OLLAMA_URL', 'http://localhost:11434 (default)')}")

    # تحقق إن الـ .env موجود
    from pathlib import Path
    env_path = Path(".env")
    if env_path.exists():
        print(f"   .env file      : ✅ found at {env_path.resolve()}")
    else:
        print(f"   .env file      : ❌ NOT FOUND at {env_path.resolve()}")
        print("   ⚠️  Run the script from the project root directory!")


if __name__ == "__main__":
    print("🧪 AIClient Test Suite")
    print("=" * 45)

    show_config()

    results = {}
    for p in LLMProvider:
        results[p] = test_single_provider(p)

    test_full_chain()

    print(f"\n{'='*45}")
    print("📋 Summary:")
    for p, ok in results.items():
        print(f"   {'✅' if ok else '❌'} {p.value}")
    print()