"""
DIAGNOSTIC SCRIPT -- analyst_engine step-by-step timing.
Run from the project root:
  .venv\Scripts\python.exe scripts/diag_analyst_timing.py

Reports elapsed time for every sub-step in /api/analyst.
Remove this file after diagnosis.
"""
from __future__ import annotations

import os
import sys
import time
import json
import decimal
import concurrent.futures
from pathlib import Path
from dotenv import load_dotenv

# Force UTF-8 output on Windows consoles
sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from sqlalchemy import text
from api.database import readonly_engine
from ai.sql_validator import validate_sql
from ai.nl_interpreter import interpret_question, build_query_from_intent

QUESTION = "top 3 least reviewed products"

def ts():
    return time.perf_counter()


def sanitize(data):
    if isinstance(data, list):
        return [sanitize(i) for i in data]
    if isinstance(data, dict):
        return {k: sanitize(v) for k, v in data.items()}
    if isinstance(data, (decimal.Decimal, float)):
        return float(data)
    if hasattr(data, "isoformat"):
        return data.isoformat()
    return data


print(f"\n{'='*60}")
print(f"DIAGNOSTIC: POST /api/analyst")
print(f"Question : \"{QUESTION}\"")
print(f"{'='*60}\n")

timings = {}
total_start = ts()

# -- Step 1: NL intent interpretation (Gemini call #1) ----------------------
t0 = ts()
print("[1] Calling interpret_question (may hit Gemini)...")
intent = interpret_question(QUESTION)
timings["1_interpret_question"] = ts() - t0
print(f"    -> intent: {intent}")
print(f"    -> elapsed: {timings['1_interpret_question']:.3f}s\n")

# -- Step 2: Query construction ---------------------------------------------
t0 = ts()
print("[2] Building query from intent...")
if intent:
    built_sql = build_query_from_intent(intent)
    print(f"    -> built_sql from intent: {built_sql}")
else:
    built_sql = None
    print("    -> intent was None; will use route_question_intent fallback")
timings["2_query_construction"] = ts() - t0
print(f"    -> elapsed: {timings['2_query_construction']:.3f}s\n")

# -- Step 3: SQL safety validation -------------------------------------------
t0 = ts()
print("[3] Running SQL safety validation (via route_question_intent)...")
from ai.analyst_engine import route_question_intent
intent_type, sql_or_source, source_desc = route_question_intent(QUESTION)
print(f"    -> route_question_intent: intent_type={intent_type}, source={source_desc}")
is_valid, clean_sql_or_err = validate_sql(sql_or_source)
timings["3_sql_validation"] = ts() - t0
print(f"    -> is_valid={is_valid}")
print(f"    -> sql: {clean_sql_or_err[:120] if is_valid else clean_sql_or_err}")
print(f"    -> elapsed: {timings['3_sql_validation']:.3f}s\n")

# -- Step 4: PostgreSQL execution --------------------------------------------
t0 = ts()
print("[4] Executing SQL against PostgreSQL...")
query_data = []
if is_valid:
    try:
        with readonly_engine.connect() as conn:
            result = conn.execute(text(clean_sql_or_err))
            query_data = [sanitize(dict(r)) for r in result]
        print(f"    -> rows returned: {len(query_data)}")
    except Exception as e:
        print(f"    -> ERROR: {e}")
timings["4_postgres_execution"] = ts() - t0
print(f"    -> elapsed: {timings['4_postgres_execution']:.3f}s\n")

# -- Step 5: Gemini answer synthesis (Gemini call #2) -----------------------
t0 = ts()
print("[5] Calling Gemini for answer synthesis (Gemini call #2)...")
api_key = os.environ.get("LLM_API_KEY") or os.environ.get("GEMINI_API_KEY")
model_name = os.environ.get("LLM_MODEL", "gemini-2.5-flash")
print(f"    -> api_key present: {bool(api_key)}")
print(f"    -> model: {model_name}")

answer = "(synthesis skipped)"
if api_key and query_data:
    try:
        from google import genai
        client = genai.Client(api_key=api_key)
        SYSTEM_PROMPT = "You are an expert AI E-Commerce Business Analyst. Answer concisely."
        prompt = (
            f'User Question: "{QUESTION}"\n'
            f"Source: {source_desc}\n"
            f"Data:\n{json.dumps(query_data[:5], indent=2)}\n"
            "Provide a brief executive answer in Markdown."
        )

        def _gen():
            return client.models.generate_content(
                model=model_name,
                contents=[SYSTEM_PROMPT, prompt],
            )

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(_gen)
            resp = future.result(timeout=45)
        answer = resp.text[:200] if resp.text else "(empty)"
        print(f"    -> Gemini responded OK: {answer[:100]}...")
    except concurrent.futures.TimeoutError:
        print("    -> TIMEOUT after 45s!")
        answer = "TIMEOUT"
    except Exception as e:
        print(f"    -> ERROR: {e}")
        answer = f"ERROR: {e}"
timings["5_gemini_synthesis"] = ts() - t0
print(f"    -> elapsed: {timings['5_gemini_synthesis']:.3f}s\n")

# -- Step 6: Response construction -------------------------------------------
t0 = ts()
response_payload = {
    "question": QUESTION,
    "answer": answer,
    "data": query_data[:20],
    "source": source_desc,
    "sql_used": clean_sql_or_err if is_valid else None,
    "insights": ["Data grounded in PostgreSQL analytics warehouse views."],
}
timings["6_response_construction"] = ts() - t0
print(f"[6] Response constructed -> elapsed: {timings['6_response_construction']:.6f}s\n")

# -- Summary -----------------------------------------------------------------
total = ts() - total_start

step_labels = {
    "1_interpret_question":    "Intent interpretation (Gemini #1)",
    "2_query_construction":    "Query construction           ",
    "3_sql_validation":        "SQL validation               ",
    "4_postgres_execution":    "PostgreSQL execution         ",
    "5_gemini_synthesis":      "Gemini synthesis (Gemini #2) ",
    "6_response_construction": "Response construction        ",
}

print(f"\n{'='*60}")
print("TIMING SUMMARY")
print(f"{'='*60}")
print(f"  {'Step':<42} {'Time':>8}")
print(f"  {'-'*50}")
for key, label in step_labels.items():
    print(f"  {label:<42} {timings[key]:>7.3f}s")
print(f"  {'-'*50}")
print(f"  {'TOTAL':<42} {total:>7.3f}s")
print(f"{'='*60}")

worst_key = max(timings, key=timings.get)
worst_time = timings[worst_key]
gemini_total = timings["1_interpret_question"] + timings["5_gemini_synthesis"]

print(f"\nGemini call #1 (interpret_question): {timings['1_interpret_question']:.3f}s")
print(f"Gemini call #2 (synthesis):          {timings['5_gemini_synthesis']:.3f}s")
print(f"COMBINED Gemini time:                {gemini_total:.3f}s")

print(f"\nBOTTLENECK: {step_labels[worst_key].strip()} ({worst_time:.3f}s)")
if gemini_total > 20:
    print("NOTE: Two Gemini calls are being made per request.")
    print("      If Gemini is the bottleneck, eliminating one call may halve latency.")

print("\nSEPARATE ISSUE DETECTED:")
print("  analyst_engine.execute_safe_query() has an empty 'try:' block (IndentationError).")
print("  The server is running from a stale .pyc cache. Queries appear to work but")
print("  execute_safe_query() likely raises an exception on every call, causing silent")
print("  fallback to the fallback route or 500 errors that FastAPI wraps as timeouts.")
