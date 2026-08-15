"""
One-shot Phase 9 test runner: executes the entire pytest suite and prints a
compact summary. Fails with non-zero exit code if anything regresses.

Usage: .venv\\Scripts\\python.exe scripts/run_all_tests.py
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

if __name__ == "__main__":
    cmd = [sys.executable, "-m", "pytest", "tests", "-q", "--tb=short"]
    print(f"$ {' '.join(cmd)}\n", flush=True)
    rc = subprocess.call(cmd, cwd=ROOT)
    sys.exit(rc)
