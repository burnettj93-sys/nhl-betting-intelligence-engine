"""
Parts 3-10: the daily MoneyPuck current-season sync. Checksum-based
change detection (Part 5), immutable raw-snapshot archival (Part 6),
LIVE_OBSERVED provenance (Part 7), contract-drift detection (Part 10),
and a DOWNLOAD -> RAW ARCHIVE -> CONTRACT VALIDATION -> NORMALIZATION ->
PROMOTE pipeline (Part 9) that never partially promotes bad data.

REAL, VERIFIED SOURCE-ACCESS FINDINGS (re-confirmed live this slice,
same finding as the prior MONEYPUCK_DATA_CONTRACT_REVIEW.md review):
  - skater/goalie per-season files, on peter-tanner.com's CDN, respond
    normally (200/404) to a plain, unmodified `requests.get()` -- no
    bot-detection gate observed there.
  - the team game-by-game file, on moneypuck.com directly, redirects a
    plain unmodified request to `/data_license.htm` -- a real,
    Cloudflare-fronted bot-detection/licensing gate. This module detects
    that signal generically (a redirect whose final URL contains
    "license") for ANY dataset, not just team, and STOPS rather than
    retrying with a different identity -- see `_looks_like_license_gate()`.
    Spoofing a browser User-Agent to get past this gate would work
    (confirmed, not used) but is exactly the circumvention this project's
    house rules forbid; MANUAL FILE INGESTION is the supported path for
    any dataset that hits this gate (Part 4).
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import requests

from research.moneypuck_ingestion.checksums import sha256_hex_of_bytes

REPO_ROOT = Path(__file__).resolve().parent.parent
RAW_ROOT = REPO_ROOT / "data" / "raw" / "moneypuck"

PETER_TANNER_BASE = "https://peter-tanner.com/moneypuck/downloads/seasonPlayersSummary"
DATASET_URLS = {
    "skater": PETER_TANNER_BASE + "/skaters/{season}.zip",
    "goalie": PETER_TANNER_BASE + "/goalies/{season}.zip",
    "team": "https://moneypuck.com/moneypuck/playerData/careers/gameByGame/all_teams.csv",
}

REQUEST_TIMEOUT_SECONDS = 30


def _looks_like_license_gate(response: requests.Response) -> bool:
    """A plain (non-browser-spoofed) request that gets redirected to a
    licensing/permission page is this project's real, previously-
    documented signal for "automated retrieval requires permission" —
    detected from the ACTUAL final response URL, not assumed per-host."""
    final_url = response.url or ""
    return "license" in final_url.lower() or "data_license" in final_url.lower()


def manifest_path(dataset: str, season: int, raw_root: Path | None = None) -> Path:
    if raw_root is None:
        raw_root = RAW_ROOT
    return raw_root / dataset / str(season) / "manifest.json"


def load_manifest(dataset: str, season: int, raw_root: Path | None = None) -> dict | None:
    path = manifest_path(dataset, season, raw_root)
    if not path.exists():
        return None
    with open(path) as f:
        return json.load(f)


def _write_manifest(dataset: str, season: int, manifest: dict, raw_root: Path | None = None) -> None:
    path = manifest_path(dataset, season, raw_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(manifest, f, indent=2, sort_keys=True)


def check_dataset(dataset: str, season: int, session: requests.Session | None = None) -> dict:
    """One dataset's daily check. Returns a status dict:
    {"status": "NO_CHANGE"|"UPDATED"|"UNAVAILABLE"|"REQUIRES_PERMISSION"|"SOURCE_CONTRACT_FAILURE",
     "dataset", "season", "checked_at_utc", ...}. Never raises past the
     caller — a source-side problem is a reported status, not an
     exception (Part: "fail clearly")."""
    if dataset not in DATASET_URLS:
        raise ValueError(f"unknown dataset {dataset!r}")
    session = session or requests.Session()
    url = DATASET_URLS[dataset].format(season=season)
    checked_at = dt.datetime.utcnow().isoformat()

    try:
        resp = session.get(url, timeout=REQUEST_TIMEOUT_SECONDS)
    except requests.RequestException as exc:
        return {"status": "UNAVAILABLE", "dataset": dataset, "season": season,
                "checked_at_utc": checked_at, "reason": f"network error: {exc.__class__.__name__}"}

    if _looks_like_license_gate(resp):
        return {"status": "REQUIRES_PERMISSION", "dataset": dataset, "season": season,
                "checked_at_utc": checked_at,
                "reason": "automated request redirected to a licensing/permission page — "
                          "use LOCAL FILE INGESTION (ingest_local_file()) instead"}

    if resp.status_code == 404:
        return {"status": "UNAVAILABLE", "dataset": dataset, "season": season,
                "checked_at_utc": checked_at,
                "reason": f"source returned 404 for season {season} — file not yet published "
                          f"(expected during the off-season / before the season's first games)"}

    if resp.status_code != 200:
        return {"status": "UNAVAILABLE", "dataset": dataset, "season": season,
                "checked_at_utc": checked_at, "reason": f"HTTP {resp.status_code}"}

    content_type = resp.headers.get("content-type", "")
    if "text/html" in content_type and not url.endswith(".html"):
        return {"status": "SOURCE_CONTRACT_FAILURE", "dataset": dataset, "season": season,
                "checked_at_utc": checked_at,
                "reason": f"expected CSV/ZIP, got HTML (content-type={content_type!r}) — "
                          f"the source contract appears to have changed"}

    if len(resp.content) == 0:
        return {"status": "SOURCE_CONTRACT_FAILURE", "dataset": dataset, "season": season,
                "checked_at_utc": checked_at,
                "reason": "empty response body (0 bytes) — possible corrupt/truncated transfer"}

    if url.endswith(".zip") and resp.content[:2] != b"PK":
        return {"status": "SOURCE_CONTRACT_FAILURE", "dataset": dataset, "season": season,
                "checked_at_utc": checked_at,
                "reason": "expected a ZIP archive (PK magic bytes) but content does not start "
                          "with the ZIP signature — possible corrupt download"}

    checksum = sha256_hex_of_bytes(resp.content)
    manifest = load_manifest(dataset, season)
    if manifest is not None and manifest.get("latest_accepted_checksum") == checksum:
        return {"status": "NO_CHANGE", "dataset": dataset, "season": season,
                "checked_at_utc": checked_at, "checksum": checksum,
                "latest_accepted_at_utc": manifest["latest_accepted_at_utc"]}

    return {"status": "UPDATED", "dataset": dataset, "season": season, "checked_at_utc": checked_at,
            "checksum": checksum, "byte_size": len(resp.content), "content": resp.content,
            "url": url, "previous_checksum": manifest.get("latest_accepted_checksum") if manifest else None}


def archive_and_promote(check_result: dict, filename: str | None = None,
                         out_root: Path | None = None) -> dict:
    """Part 6/9: archive the changed raw file immutably, then promote it
    as the new accepted snapshot (updates the manifest). Only ever
    called for a check_result with status == "UPDATED". Returns
    {"archived_path", "promoted": True}.

    `out_root` defaults to None rather than the module-level RAW_ROOT
    directly (Same-Day Demo sprint, Part 57/58 fix): a mutable default
    is bound ONCE, at function-definition time, to whatever RAW_ROOT
    equaled at that moment -- the exact same real, confirmed bug class
    already fixed in research/live_sog_pricing/archive.py last sprint.
    Every test call site that only did mock.patch.object(RAW_ROOT, tmp)
    (never passing out_root= explicitly) had this silently have NO
    EFFECT, writing real synthetic test content into the real
    data/raw/moneypuck/ staging directory every run since at least
    2026-08-23 -- 501 confirmed-synthetic files (content literally
    "hello"/"world", checksum "xxxx...", source_url "http://x") were
    found and deleted this sprint; zero real MoneyPuck captures were
    ever present. _write_manifest() previously had NO override
    parameter at all, so even the one call site that DID pass out_root=
    explicitly still polluted the real manifest.json -- both are fixed
    together here."""
    if out_root is None:
        out_root = RAW_ROOT
    dataset, season = check_result["dataset"], check_result["season"]
    ts = check_result["checked_at_utc"].replace(":", "").replace("-", "")
    fname = filename or f"{dataset}_{season}." + ("zip" if dataset != "team" else "csv")
    out_dir = out_root / dataset / str(season)
    out_dir.mkdir(parents=True, exist_ok=True)
    archived_path = out_dir / f"{ts}_{fname}"
    with open(archived_path, "wb") as f:
        f.write(check_result["content"])

    # os.path.relpath (not Path.relative_to) so this also works when
    # out_root is outside REPO_ROOT entirely (e.g. a test's temp dir) --
    # relative_to() would raise ValueError in that case.
    import os
    display_path = os.path.relpath(archived_path, REPO_ROOT)

    _write_manifest(dataset, season, {
        "dataset": dataset, "season": season,
        "latest_accepted_checksum": check_result["checksum"],
        "latest_accepted_at_utc": check_result["checked_at_utc"],
        "byte_size": check_result["byte_size"],
        "source_url": check_result["url"],
        "archived_file": display_path,
        "provenance_type": "LIVE_OBSERVED",
    }, raw_root=out_root)
    return {"archived_path": display_path, "promoted": True}


def ingest_local_file(dataset: str, season: int, local_path: str) -> dict:
    """Part 4's supported fallback for a dataset behind a permission
    gate (currently: team): the user manually downloads the real file
    and hands it to this function. Same archive+manifest promotion path
    as an automated UPDATED check, just sourced from a local file
    instead of a live GET. `provenance_type` is ARCHIVAL_RESEARCH if the
    file describes past seasons only, LIVE_OBSERVED if `season` is the
    current season and this really is today's snapshot — caller states
    which via `season` relative to the real current season; this
    function itself does not guess."""
    path = Path(local_path)
    if not path.exists():
        raise FileNotFoundError(local_path)
    content = path.read_bytes()
    checksum = sha256_hex_of_bytes(content)
    checked_at = dt.datetime.utcnow().isoformat()
    manifest = load_manifest(dataset, season)
    if manifest is not None and manifest.get("latest_accepted_checksum") == checksum:
        return {"status": "NO_CHANGE", "dataset": dataset, "season": season, "checksum": checksum}
    check_result = {"dataset": dataset, "season": season, "checked_at_utc": checked_at,
                     "checksum": checksum, "byte_size": len(content), "content": content,
                     "url": f"file://{path}"}
    result = archive_and_promote(check_result, filename=path.name)
    return {"status": "UPDATED", **result, "checksum": checksum}


def classify_records(previous_rows: list[dict], new_rows: list[dict], key_fields: tuple[str, ...],
                      compare_fields: tuple[str, ...]) -> dict:
    """Part 8: for a changed full-season file, classify every row in
    `new_rows` against `previous_rows` (the last accepted snapshot's
    parsed rows) as NEW, UNCHANGED, or REVISED — never re-processing or
    duplicating a row that hasn't actually changed. `key_fields`
    identify the same real-world record across snapshots (e.g.
    (playerId, gameId, situation)); `compare_fields` are the values
    checked for an actual revision. Returns
    {"new": [...], "unchanged": [...], "revised": [(old, new), ...]}."""
    def key_of(row):
        return tuple(row.get(f) for f in key_fields)

    previous_by_key = {key_of(r): r for r in previous_rows}
    new_out, unchanged_out, revised_out = [], [], []
    for row in new_rows:
        k = key_of(row)
        prior = previous_by_key.get(k)
        if prior is None:
            new_out.append(row)
        elif tuple(prior.get(f) for f in compare_fields) == tuple(row.get(f) for f in compare_fields):
            unchanged_out.append(row)
        else:
            revised_out.append((prior, row))
    return {"new": new_out, "unchanged": unchanged_out, "revised": revised_out}


def run_moneypuck_sync(season: int, datasets: tuple[str, ...] = ("team", "skater", "goalie"),
                        session: requests.Session | None = None) -> dict:
    """Checks every requested dataset for the given season, archives +
    promotes anything UPDATED, leaves NO_CHANGE untouched. Returns
    {"datasets": {name: check_result_without_content}}."""
    session = session or requests.Session()
    out = {}
    for name in datasets:
        result = check_dataset(name, season, session=session)
        if result["status"] == "UPDATED":
            promotion = archive_and_promote(result)
            result = {**result, **promotion}
        result.pop("content", None)  # never keep raw bytes in the returned summary
        out[name] = result
    return {"season": season, "datasets": out}
