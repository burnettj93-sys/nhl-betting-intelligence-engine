#!/usr/bin/env python3
"""Checks The Odds API credential state WITHOUT ever printing a key.

    python3 deploy/verify_odds_key.py

Uses the free `/v4/sports/` endpoint (no quota is consumed) and reports, for each key, only a short SHA-256
fingerprint, the HTTP status, and the quota header. It checks:
  1. the CONFIGURED key (the same source the jobs use: environment variable or `.env`) works;
  2. every EXPOSED candidate key found in git history (32-hex tokens on secret-looking lines of the old test files
     that once contained the live key) is REJECTED. A rejected old key (401/403) means rotation took effect.

Exit code 0 only when the configured key works and no exposed candidate still works.
"""
from __future__ import annotations

import hashlib
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

# Commits whose versions of these files held the live key as a string literal (found with `git log -S`).
HISTORY = (("134abee", "tests/test_cloud_live_data.py"), ("6335ce3", "tests/test_operational_daily_sync.py"),
           ("6335ce3", "tests/test_cloud_live_data.py"))
HEX32 = re.compile(r"(?<![0-9a-fA-F])[0-9a-fA-F]{32}(?![0-9a-fA-F])")
DUMMY = {"deadbeef" * 4}


def fingerprint(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()[:10]


def probe(key: str) -> dict:
    import requests   # the same HTTP stack (and CA bundle) the production client uses
    try:
        resp = requests.get("https://api.the-odds-api.com/v4/sports/", params={"apiKey": key}, timeout=20)
        return {"status": resp.status_code, "requests_remaining": resp.headers.get("x-requests-remaining")}
    except requests.RequestException as exc:  # network trouble is not a verdict about the key
        return {"status": None, "error": exc.__class__.__name__}


def configured_key() -> str | None:
    from research.live_sog_pricing.env_config import get_the_odds_api_key
    return get_the_odds_api_key()


def exposed_candidates() -> set[str]:
    found: set[str] = set()
    for rev, path in HISTORY:
        try:
            text = subprocess.run(["git", "show", f"{rev}:{path}"], cwd=REPO, capture_output=True, text=True, check=True).stdout
        except subprocess.CalledProcessError:
            continue
        for line in text.splitlines():
            if re.search(r"secret|prefix|api_?key|THE_ODDS", line, re.I):
                found.update(m.lower() for m in HEX32.findall(line))
    return {k for k in found if k not in DUMMY}


def main() -> int:
    ok = True
    key = configured_key()
    if not key:
        print("configured key: NONE FOUND (no THE_ODDS_API_KEY in the environment or .env)")
        return 1
    result = probe(key)
    works = result["status"] == 200
    print(f"configured key  fp={fingerprint(key)}  http={result['status']}  quota_remaining={result.get('requests_remaining')}"
          f"  -> {'WORKS' if works else 'DOES NOT WORK'}")
    ok &= works
    exposed = exposed_candidates()
    if not exposed:
        print("exposed candidates: none found in git history")
    for candidate in sorted(exposed):
        r = probe(candidate)
        same = candidate == key.lower()
        live = r["status"] == 200
        print(f"exposed key     fp={fingerprint(candidate)}  http={r['status']}  same_as_configured={same}"
              f"  -> {'STILL ACTIVE: revoke it' if live else 'rejected (revoked or invalid)' if r['status'] in (401, 403) else 'inconclusive'}")
        ok &= (r["status"] in (401, 403)) and not same
    print("RESULT:", "rotation verified" if ok else "NOT RESOLVED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
