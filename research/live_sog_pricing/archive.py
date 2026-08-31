"""
Preserves every genuine The Odds API response used for development,
unchanged, under data/raw/the_odds_api/live/ -- so parser/normalization
work can be re-run against captured real payloads instead of spending
API credits again. NEVER stores the API key (client.ApiResult never
carries it in the first place -- see client.py's docstring).
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from research.live_sog_pricing.client import ApiResult

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
ARCHIVE_DIR = REPO_ROOT / "data" / "raw" / "the_odds_api" / "live"


def _checksum(payload_json_text: str) -> str:
    return hashlib.sha256(payload_json_text.encode("utf-8")).hexdigest()


def archive_result(result: ApiResult, *, event_id: str | None, market_filter: str | None,
                    bookmaker_filter: str | None, out_dir: Path | None = None) -> Path:
    """Writes one archive file: {"meta": {...}, "response": <raw data>}.
    Filename: <retrieved_at_utc>_<event_id_or_'sportlist'>_<market_or_'none'>.json

    `out_dir` defaults to None rather than the module-level ARCHIVE_DIR
    directly (Preseason Operational Readiness Closure sprint, Part 5/6/7
    fix): a mutable default is bound ONCE, at function-definition time,
    to whatever ARCHIVE_DIR equaled at that moment -- so
    mock.patch("research.live_sog_pricing.archive.ARCHIVE_DIR", tmp_dir)
    in a test silently has NO EFFECT on any caller that doesn't also pass
    out_dir= explicitly. This was a REAL, confirmed bug this sprint found:
    operational/live_odds_daily_pull.py's real archive_result() call sites
    (never passing out_dir=) kept writing into the real
    data/raw/the_odds_api/live/ evidence directory even from tests that
    believed they had redirected it via mock.patch -- see Part 6's cleanup
    and tests/test_evidence_directory_isolation.py's regression guard.
    Looking ARCHIVE_DIR up fresh, by name, inside the function body (never
    baked into the signature) is what makes mock.patch on the module
    attribute actually take effect."""
    if out_dir is None:
        out_dir = ARCHIVE_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    payload_text = json.dumps(result.data, sort_keys=True)
    meta = {
        "retrieved_at_utc": result.retrieved_at_utc,
        "endpoint": result.endpoint,
        "event_id": event_id,
        "sport": "icehockey_nhl",
        "bookmaker_filter": bookmaker_filter,
        "market_filter": market_filter,
        "response_checksum_sha256": _checksum(payload_text),
        "requests_used_header": result.requests_used,
        "requests_remaining_header": result.requests_remaining,
        "requests_last_header": result.requests_last,
        "http_status": result.status_code,
    }
    safe_ts = result.retrieved_at_utc.replace(":", "").replace("-", "")
    # endpoint-derived tag distinguishes /sports from /sports/.../events (both
    # have event_id=None, market_filter=None -- a same-second timestamp
    # collision between the two previously overwrote one archive file
    # silently; the endpoint tag plus a numeric de-dupe suffix below fixes
    # this for good, not just for this specific pair of endpoints).
    endpoint_tag = result.endpoint.strip("/").replace("/", "-") or "unknown"
    fname_base = f"{safe_ts}_{endpoint_tag}_{event_id or 'na'}_{(market_filter or 'none').replace(',', '+')}"
    path = out_dir / f"{fname_base}.json"
    suffix = 2
    while path.exists():
        path = out_dir / f"{fname_base}_{suffix}.json"
        suffix += 1
    with open(path, "w") as f:
        json.dump({"meta": meta, "response": result.data}, f, indent=2, sort_keys=True)
    return path


def load_archived(path: Path) -> dict:
    with open(path) as f:
        return json.load(f)
