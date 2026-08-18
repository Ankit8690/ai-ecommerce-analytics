"""
Deterministic response cache for Gemini calls.

Given the same (model, prompt) pair, Gemini's answer is stable enough within a
single day that we can cache it. This turns repeat questions (interview demos,
common recurring queries) into instant responses AND spares the free-tier
20-request/day quota.

Design:
  * Keys are SHA-256 of "model|prompt" — collision-resistant, no plaintext prompt on disk.
  * Values are JSON files under `.cache/gemini/` (git- and docker-ignored).
  * TTL default 24 h — safe upper bound; the DB data feeding the prompt changes
    slowly. Set to 0 to disable, or via env `GEMINI_CACHE_TTL`.
  * Failure is silent: cache errors never break a real Gemini call.

Public API:
    get(model, prompt) -> Optional[str]
    put(model, prompt, text) -> None
    stats() -> dict
    clear() -> int
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Optional

_ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = _ROOT / ".cache" / "gemini"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

_DEFAULT_TTL = 24 * 60 * 60  # 24 hours


def _ttl() -> int:
    """Effective TTL — 0 disables the cache entirely."""
    raw = os.getenv("GEMINI_CACHE_TTL")
    if raw is None:
        return _DEFAULT_TTL
    try:
        return max(0, int(raw))
    except ValueError:
        return _DEFAULT_TTL


def _key(model: str, prompt: str) -> str:
    return hashlib.sha256(f"{model}|{prompt}".encode("utf-8")).hexdigest()[:40]


def _path(key: str) -> Path:
    return CACHE_DIR / f"{key}.json"


def get(model: str, prompt: str) -> Optional[str]:
    """Return the cached text if present and not expired, else None."""
    ttl = _ttl()
    if ttl == 0:
        return None
    if not model or not prompt:
        return None
    p = _path(_key(model, prompt))
    if not p.exists():
        return None
    try:
        payload = json.loads(p.read_text(encoding="utf-8"))
        if time.time() - float(payload.get("ts", 0)) > ttl:
            return None
        text = payload.get("text")
        return text if isinstance(text, str) and text else None
    except Exception:
        return None


def put(model: str, prompt: str, text: str) -> None:
    """Store a successful response. No-op on empty text or write errors."""
    if _ttl() == 0 or not model or not prompt or not text:
        return
    try:
        _path(_key(model, prompt)).write_text(
            json.dumps({"ts": time.time(), "model": model, "text": text},
                       ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception:
        pass  # cache write failure must never break the caller


def stats() -> dict:
    """Diagnostic: number of cached entries and total bytes on disk."""
    entries = list(CACHE_DIR.glob("*.json"))
    return {
        "entries": len(entries),
        "bytes": sum(p.stat().st_size for p in entries),
        "dir": str(CACHE_DIR),
        "ttl_seconds": _ttl(),
    }


def clear() -> int:
    """Delete every cache entry. Returns the number removed."""
    n = 0
    for p in CACHE_DIR.glob("*.json"):
        try:
            p.unlink()
            n += 1
        except Exception:
            pass
    return n
