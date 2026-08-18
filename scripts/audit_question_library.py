"""
Audit every question in ai/question_library.json against the actual analyst
pipeline. Reports which questions return valid data and which fail (empty
result, SQL error, etc.). Writes a cleaned library keeping only the passes.

Runs with Gemini DISABLED so the audit tests the deterministic parser +
keyword router paths — the ones that are 100% reliable regardless of quota.

Usage:
    .venv\\Scripts\\python.exe scripts/audit_question_library.py            # dry-run report
    .venv\\Scripts\\python.exe scripts/audit_question_library.py --apply    # rewrite library
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")
# Force Gemini off so we only test paths that don't depend on external LLM.
os.environ.pop("LLM_API_KEY", None)
os.environ.pop("GEMINI_API_KEY", None)

from api.database import readonly_engine
from ai.analyst_engine import process_analyst_question

LIB_PATH = ROOT / "ai" / "question_library.json"


def is_valid_answer(result: dict) -> tuple[bool, str]:
    """A question passes if it returned rows and no error message."""
    answer = result.get("answer", "") or ""
    data = result.get("data") or []
    if answer.startswith("⚠️") or "Query error" in answer or "error" in answer.lower()[:80]:
        return False, "error in answer"
    if not data:
        return False, "0 rows returned"
    return True, "ok"


def main() -> int:
    apply = "--apply" in sys.argv
    payload = json.loads(LIB_PATH.read_text(encoding="utf-8"))
    questions = payload.get("questions", [])
    print(f"Auditing {len(questions)} questions from ai/question_library.json")
    print(f"Mode: {'APPLY (will rewrite library)' if apply else 'DRY-RUN (report only)'}\n")

    passes, fails = [], []
    for q in questions:
        try:
            result = process_analyst_question(readonly_engine, q["question"])
            ok, reason = is_valid_answer(result)
        except Exception as e:
            ok, reason = False, f"exception: {type(e).__name__}"

        if ok:
            passes.append(q)
            print(f"  [OK] Q{q['id']:3d} {q['category']:24s} {q['question'][:60]}")
        else:
            fails.append((q, reason))
            print(f"  [--] Q{q['id']:3d} {q['category']:24s} {q['question'][:50]} — {reason}")

    print(f"\n{'='*70}")
    print(f"PASS: {len(passes)}  |  FAIL: {len(fails)}  |  TOTAL: {len(questions)}")
    print(f"{'='*70}")

    if fails:
        print("\nFAILED QUESTIONS (will be removed if --apply):")
        for q, reason in fails:
            print(f"  Q{q['id']} — {q['question']}  ({reason})")

    if apply and fails:
        # Renumber IDs sequentially to keep the file tidy after removal
        for i, q in enumerate(passes, 1):
            q["id"] = i
        payload["questions"] = passes
        payload["notes"] = (payload.get("notes", "") +
                            f"  [Audited: {len(passes)} verified queries — see scripts/audit_question_library.py]").strip()
        LIB_PATH.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"\n[write] rewrote {LIB_PATH.name} with {len(passes)} verified questions.")
    elif apply:
        print("\n[write] no changes — every question passed.")
    else:
        print("\nRun with --apply to overwrite the library keeping only passing questions.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
