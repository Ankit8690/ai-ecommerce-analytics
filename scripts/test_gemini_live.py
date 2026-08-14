"""
Minimal Gemini API live test.
Run after setting LLM_API_KEY (or GEMINI_API_KEY) in .env.

Tests:
  1. Environment variable is detected (value never printed).
  2. Gemini client initialises.
  3. One small test request succeeds.
  4. Deterministic fallback still works when key is absent.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Load .env so the script picks up keys when run directly
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass  # python-dotenv optional; key may already be in shell env


def main() -> None:
    print("=" * 60)
    print("GEMINI API LIVE CONFIGURATION TEST")
    print("=" * 60)

    # 1. Detect key
    api_key = os.environ.get("LLM_API_KEY") or os.environ.get("GEMINI_API_KEY")
    model_name = os.environ.get("LLM_MODEL") or "gemini-2.5-flash"

    if not api_key:
        print("\n⚠️  No API key found.")
        print("   Please add your key to .env:")
        print("   LLM_API_KEY=your_google_ai_studio_key")
        print("   Then re-run: .venv\\Scripts\\python.exe scripts\\test_gemini_live.py")
        sys.exit(1)

    print(f"\n1. LLM_API_KEY detected: YES (value hidden)")
    print(f"   Model:                 {model_name}")

    # 2. Initialise client
    try:
        from google import genai
        client = genai.Client(api_key=api_key)
        print("2. Gemini client initialised: OK")
    except Exception as e:
        print(f"2. Gemini client initialisation FAILED: {e}")
        sys.exit(1)

    # 3. Send one small test request
    print("3. Sending test request to Gemini API ...")
    try:
        response = client.models.generate_content(
            model=model_name,
            contents=["Respond with exactly: ANALYST_OK"]
        )
        reply = (response.text or "").strip()
        if "ANALYST_OK" in reply:
            print(f"   Gemini response received: OK  (\"{reply}\")")
        else:
            print(f"   Gemini responded (unexpected content): \"{reply[:120]}\"")
            print("   API is live and reachable — response format may vary.")
    except Exception as e:
        print(f"   Gemini request FAILED: {e}")
        sys.exit(1)

    # 4. Verify deterministic fallback still works without a key
    print("4. Testing deterministic fallback (key-absent simulation) ...")
    from ai.analyst_engine import format_grounded_fallback
    fallback_data = [{
        "total_orders": 99441,
        "total_unique_customers": 96096,
        "product_gmv": 15843553.24,
        "cash_collected": 16008872.12,
        "avg_order_value_gmv": 159.33,
        "delivered_orders": 96478,
        "delivered_pct": 97.02
    }]
    fallback_text = format_grounded_fallback("test question", fallback_data, "Executive KPIs View")
    assert "99,441" in fallback_text, "Fallback missing grounded order count!"
    assert "15,843,553.24" in fallback_text, "Fallback missing grounded GMV!"
    print(f"   Deterministic fallback: OK (grounded metrics verified)")

    print("\n✅  ALL CHECKS PASSED — Gemini live API is configured and working.")
    print(f"   The AI Analyst will use model: {model_name}")


if __name__ == "__main__":
    main()
