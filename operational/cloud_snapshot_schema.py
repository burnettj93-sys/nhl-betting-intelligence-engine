"""
Cloud snapshot schema, validation, content hashing and freshness rules
(Cloud live-data sprint, 2026-09-25).

This module is shared by the LOCAL publisher (operational/publish_cloud_snapshot.py)
and the Community Cloud READER (dashboard/snapshot_source.py), so it is
deliberately dependency-free (stdlib only) and imports no model/research code.

SCHEMA
  schema_version 1 -- the git-bundled board.json from the memory sprint
                      (demo board + live moneyline rows). Still READABLE, used
                      only as the emergency/bootstrap fallback.
  schema_version 2 -- the published "current" snapshot: v1's sections plus
                      real recommendations, performance, Morning Review, model
                      learning, ledger summary, data status and health.

PROVENANCE (project-canonical labels, never flattened together):
  LIVE — DRAFTKINGS        real DraftKings captures compared with the engine
  REAL MARKET              real-market recommendations recorded by the engine
                           (ledger / paper bankroll)
  SIMULATED — DEMO ONLY    the deterministic demo board (real model output,
                           simulated prices)

FRESHNESS is computed ONLY from factual timestamps carried in the snapshot
(metadata.data_as_of), never invented. See classify_freshness().
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import re

SCHEMA_VERSION = 2
SUPPORTED_SCHEMA_VERSIONS = (1, 2)

PROVENANCE_LIVE = "LIVE — DRAFTKINGS"
PROVENANCE_REAL_MARKET = "REAL MARKET"
PROVENANCE_DEMO = "SIMULATED — DEMO ONLY"

# Freshness windows, in hours since metadata.data_as_of (the newest real
# odds/recommendation refresh). The moneyline job runs 4x/day with a 12 h
# overnight gap (20:00 -> 08:00), so CURRENT must tolerate 12 h plus slack.
CURRENT_MAX_HOURS = 13.0
STALE_MAX_HOURS = 36.0

CURRENT, STALE, VERY_STALE, UNAVAILABLE = "CURRENT", "STALE", "VERY_STALE", "UNAVAILABLE"

# metadata fields that describe the PUBLICATION, not the content. Excluded from
# the content hash so a re-publication of identical content is NO_CHANGE.
PUBLICATION_ONLY_FIELDS = ("generated_at", "generated_by", "source_master_commit", "content_hash",
                           "publication_seq")
# keys that are pure compute-time stamps inside embedded reports; excluded from the hash
VOLATILE_KEYS = frozenset({"generated_at_utc", "computed_at_utc"})

REQUIRED_TOP_LEVEL = {1: ("schema_version", "meta", "demo"), 2: ("schema_version", "metadata", "demo")}
REQUIRED_METADATA_V2 = ("schema_version", "generated_at", "generated_by", "source_master_commit",
                        "engine_mode", "data_as_of", "freshness")
FRESHNESS_KEYS = ("nhl_data", "odds", "recommendations", "settlement", "postmortem")

# Anything that could be a secret / identity / filesystem exposure is rejected.
_FORBIDDEN_KEY_RE = re.compile(r"(api[_-]?key|secret|passw(or)?d|token|credential|authorization|bearer|"
                               r"client[_-]?id|client[_-]?secret|refresh[_-]?token)", re.I)
_FORBIDDEN_VALUE_RES = (
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"),                # GitHub tokens
    re.compile(r"\bsk-[A-Za-z0-9]{20,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\bBearer\s+[A-Za-z0-9._\-]{16,}"),
    re.compile(r"[?&](api[_-]?key|apikey|access[_-]?token|token)=", re.I),
)
_ABSOLUTE_PATH_RE = re.compile(r"(^|[\s\"'(=])(/Users/|/home/|/private/|/opt/|/mount/|/var/|/tmp/|[A-Za-z]:\\)")
_YAHOO_RE = re.compile(r"yahoo", re.I)


class SnapshotInvalid(ValueError):
    """The snapshot failed validation; it must not be published or displayed."""


def parse_utc(value) -> dt.datetime | None:
    """Parse an ISO-8601 timestamp (with Z or offset) to an aware UTC datetime; None if unparseable."""
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def classify_freshness(data_as_of, now: dt.datetime | None = None) -> str:
    """CURRENT / STALE / VERY_STALE from a factual timestamp; UNAVAILABLE when it
    is missing or unparseable. A timestamp in the future beyond a small clock-skew
    allowance is treated as UNAVAILABLE (never presented as fresh)."""
    parsed = parse_utc(data_as_of)
    if parsed is None:
        return UNAVAILABLE
    now = now or dt.datetime.now(dt.timezone.utc)
    age_hours = (now - parsed).total_seconds() / 3600.0
    if age_hours < -0.25:
        return UNAVAILABLE
    if age_hours <= CURRENT_MAX_HOURS:
        return CURRENT
    if age_hours <= STALE_MAX_HOURS:
        return STALE
    return VERY_STALE


# ---- MARKET / RECOMMENDATION freshness (separate from snapshot freshness) ----------------------------
# A snapshot published a minute ago can still carry a sportsbook price captured hours ago. Snapshot
# freshness (above) says how recent the PUBLICATION's data is; this says how recent one PRICE is.
# Presentation only: never alters a stored recommendation or the decision policy.
MARKET_CURRENT_MAX_MINUTES = 180.0        # ordinary odds / recommendation: <= 3 h
NEAR_GAME_WINDOW_HOURS = 4.0              # game starts within 4 h ...
NEAR_GAME_CURRENT_MAX_MINUTES = 90.0      # ... price must be <= 90 min old
FUTURE_SKEW_MINUTES = 15.0
REASON_OK, REASON_AGE, REASON_NEAR_GAME_AGE = "WITHIN_LIMIT", "OLDER_THAN_3H", "NEAR_GAME_OLDER_THAN_90M"
REASON_STARTED, REASON_NO_TS, REASON_AFTER_SNAPSHOT = "GAME_STARTED", "NO_PRICE_TIMESTAMP", "TIMESTAMP_AFTER_SNAPSHOT"
REASON_FUTURE = "TIMESTAMP_IN_FUTURE"


def classify_market_freshness(price_ts, game_start=None, now: dt.datetime | None = None,
                              snapshot_generated_at=None) -> dict:
    """CURRENT / STALE / UNAVAILABLE for one market price or recommendation.

    `price_ts` is when the sportsbook price was captured (fall back to the recommendation's created_at
    only when no price timestamp exists). Rules: age <= 180 min is CURRENT; if the game starts within
    4 h the limit tightens to 90 min; a game that has already started is never CURRENT. A missing or
    unparseable timestamp, one in the future, or one later than the snapshot that claims to contain it
    (impossible/misleading) is UNAVAILABLE -- never CURRENT."""
    now = now or dt.datetime.now(dt.timezone.utc)
    price = parse_utc(price_ts)
    start = parse_utc(game_start)
    out = {"state": UNAVAILABLE, "reason": REASON_NO_TS, "age_minutes": None, "limit_minutes": MARKET_CURRENT_MAX_MINUTES,
           "price_ts": price_ts, "game_start": game_start}
    if price is None:
        return out
    age_min = (now - price).total_seconds() / 60.0
    out["age_minutes"] = round(age_min, 1)
    generated = parse_utc(snapshot_generated_at)
    if age_min < -FUTURE_SKEW_MINUTES:
        out["reason"] = REASON_FUTURE
        return out
    if generated is not None and (price - generated).total_seconds() / 60.0 > FUTURE_SKEW_MINUTES:
        out["reason"] = REASON_AFTER_SNAPSHOT
        return out
    if start is not None:
        until_start_h = (start - now).total_seconds() / 3600.0
        if until_start_h <= 0:
            out.update(state=STALE, reason=REASON_STARTED)
            return out
        if until_start_h <= NEAR_GAME_WINDOW_HOURS:
            out["limit_minutes"] = NEAR_GAME_CURRENT_MAX_MINUTES
    if age_min <= out["limit_minutes"]:
        out.update(state=CURRENT, reason=REASON_OK)
    else:
        out.update(state=STALE, reason=REASON_NEAR_GAME_AGE if out["limit_minutes"] == NEAR_GAME_CURRENT_MAX_MINUTES
                   else REASON_AGE)
    return out


_MARKET_RANK = {CURRENT: 0, STALE: 1, VERY_STALE: 2, UNAVAILABLE: 3}


def strictest(states) -> str:
    """The worst of several freshness states (a parlay is only as fresh as its stalest leg)."""
    states = list(states)
    return max(states, key=lambda x: _MARKET_RANK.get(x, 3)) if states else UNAVAILABLE


def recommendation_freshness(row: dict, now: dt.datetime | None = None, snapshot_generated_at=None) -> dict:
    """Market freshness of one recommendation/market row plus the four timestamps a card shows."""
    price_ts = (row.get("odds_captured_at_utc") or row.get("captured_at_utc")
                or row.get("market_captured_at_utc") or row.get("created_at_utc"))
    start = row.get("event_start_utc") or row.get("commence_time_utc")
    result = classify_market_freshness(price_ts, start, now, snapshot_generated_at)
    result["created_at"] = row.get("created_at_utc")
    result["market_captured_at"] = price_ts
    result["game_start_utc"] = start
    return result


def parlay_freshness(legs: list[dict], now: dt.datetime | None = None, snapshot_generated_at=None) -> dict:
    """A Game Edge Parlay inherits its STALEST leg; no legs -> UNAVAILABLE."""
    per_leg = [recommendation_freshness(l, now, snapshot_generated_at) for l in legs]
    worst = strictest(f["state"] for f in per_leg)
    worst_leg = next((f for f in per_leg if f["state"] == worst), None)
    return {"state": worst, "legs": per_leg, "limiting_leg": worst_leg}


def age_hours(ts, now: dt.datetime | None = None) -> float | None:
    parsed = parse_utc(ts)
    if parsed is None:
        return None
    now = now or dt.datetime.now(dt.timezone.utc)
    return round((now - parsed).total_seconds() / 3600.0, 2)


def metadata_of(doc: dict) -> dict:
    """Uniform metadata view over v1 (`meta`) and v2 (`metadata`) documents."""
    if doc.get("schema_version") == 1:
        meta = dict(doc.get("meta") or {})
        # v1 has no data_as_of: its factual data time is the newest real DK capture (may be None)
        meta.setdefault("data_as_of", meta.get("newest_real_dk_capture_utc"))
        meta.setdefault("generated_at", meta.get("generated_at_utc"))
        return meta
    return dict(doc.get("metadata") or {})


def _walk(node, path=""):
    """Yield (path, key_or_None, value) for every node; keys yielded for dict members."""
    if isinstance(node, dict):
        for k, v in node.items():
            yield f"{path}/{k}", str(k), v
            yield from _walk(v, f"{path}/{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _walk(v, f"{path}[{i}]")


def strict_dumps(doc, *, indent=None) -> str:
    """JSON with NaN/Infinity rejected (allow_nan=False) and stable key order."""
    try:
        return json.dumps(doc, allow_nan=False, sort_keys=True, ensure_ascii=False,
                          separators=(",", ":") if indent is None else None, indent=indent)
    except ValueError as exc:  # NaN / Infinity
        raise SnapshotInvalid(f"snapshot contains NaN/Infinity or a non-serializable value: {exc}") from exc
    except TypeError as exc:
        raise SnapshotInvalid(f"snapshot contains a non-serializable value: {exc}") from exc


def _reject_non_finite(doc) -> None:
    for path, _key, value in _walk(doc):
        if isinstance(value, float) and not math.isfinite(value):
            raise SnapshotInvalid(f"non-finite number at {path}")


def validate_snapshot(doc, *, known_secrets: tuple[str, ...] = (), for_publication: bool = False) -> dict:
    """Validate a parsed snapshot; returns it unchanged, or raises SnapshotInvalid.

    Always: dict shape, supported schema_version, required sections/fields,
    parseable timestamps, finite numbers.
    for_publication=True additionally enforces the secret/identity rules that
    keep private material out of a (possibly world-readable) data branch:
    no forbidden keys, no known secret VALUES (any string >= 6 chars passed in
    `known_secrets`), no credential-looking strings, no absolute filesystem
    paths, and STRUCTURALLY no Yahoo content of any kind.
    """
    if not isinstance(doc, dict):
        raise SnapshotInvalid("snapshot is not a JSON object")
    version = doc.get("schema_version")
    if version not in SUPPORTED_SCHEMA_VERSIONS:
        raise SnapshotInvalid(f"unsupported schema_version {version!r} (supported: {SUPPORTED_SCHEMA_VERSIONS})")
    for key in REQUIRED_TOP_LEVEL[version]:
        if key not in doc:
            raise SnapshotInvalid(f"missing required top-level section {key!r}")
    if not isinstance(doc["demo"], dict):
        raise SnapshotInvalid("`demo` must be an object")
    meta = metadata_of(doc)
    if version == 2:
        for field in REQUIRED_METADATA_V2:
            if field not in meta:
                raise SnapshotInvalid(f"metadata missing required field {field!r}")
        if not isinstance(meta["freshness"], dict):
            raise SnapshotInvalid("metadata.freshness must be an object")
        for ts_name in ("generated_at", "data_as_of"):
            if meta.get(ts_name) is not None and parse_utc(meta[ts_name]) is None:
                raise SnapshotInvalid(f"metadata.{ts_name} is not a parseable timestamp: {meta[ts_name]!r}")
        if parse_utc(meta["generated_at"]) is None:
            raise SnapshotInvalid("metadata.generated_at is required and must be a timestamp")
        for key, value in meta["freshness"].items():
            if value is not None and parse_utc(value) is None:
                raise SnapshotInvalid(f"metadata.freshness.{key} is not a parseable timestamp: {value!r}")
    _reject_non_finite(doc)

    if for_publication:
        blob = strict_dumps(doc)
        for path, key, value in _walk(doc):
            if _FORBIDDEN_KEY_RE.search(key):
                raise SnapshotInvalid(f"forbidden key {key!r} at {path}")
            if _YAHOO_RE.search(key):
                raise SnapshotInvalid(f"Yahoo content is structurally excluded (key {key!r} at {path})")
            if isinstance(value, str):
                if _YAHOO_RE.search(value):
                    raise SnapshotInvalid(f"Yahoo content is structurally excluded (value at {path})")
                for rx in _FORBIDDEN_VALUE_RES:
                    if rx.search(value):
                        raise SnapshotInvalid(f"credential-looking string at {path}")
                if _ABSOLUTE_PATH_RE.search(value):
                    raise SnapshotInvalid(f"absolute filesystem path at {path}")
        for secret in known_secrets:
            if secret and len(secret) >= 6 and secret in blob:
                raise SnapshotInvalid("a configured secret value appears in the snapshot")
    return doc


def _strip_volatile(node):
    if isinstance(node, dict):
        return {k: _strip_volatile(v) for k, v in node.items() if k not in VOLATILE_KEYS}
    if isinstance(node, list):
        return [_strip_volatile(v) for v in node]
    return node


# Ingestion-health components whose timestamps change WITHOUT any change in what the Cloud shows: the publisher's
# own bookkeeping (each publish rewrote it, so the very next publish always looked "changed" and NO_CHANGE could
# never occur) and the 30-minute / daily NHL schedule refreshes. Their per-run timestamps are excluded from the
# content hash only; the published document still carries them.
HASH_IGNORED_INGESTION_COMPONENTS = ("cloud_snapshot_publish", "nhl_pregame_targeted_refresh", "nhl_midday_schedule_refresh")
_INGESTION_STAMP_FIELDS = ("last_attempt_utc", "last_success_utc")


def canonical_for_hash(doc: dict) -> dict:
    """The document with publication-only metadata and compute-time stamps removed."""
    out = _strip_volatile(json.loads(strict_dumps(doc)))
    ingestion = ((out.get("data_status") or {}).get("ingestion_health")) if isinstance(out.get("data_status"), dict) else None
    if isinstance(ingestion, dict):
        for comp in HASH_IGNORED_INGESTION_COMPONENTS:
            if isinstance(ingestion.get(comp), dict):
                for f in _INGESTION_STAMP_FIELDS:
                    ingestion[comp].pop(f, None)
    meta_key = "metadata" if "metadata" in out else "meta"
    if isinstance(out.get(meta_key), dict):
        for field in PUBLICATION_ONLY_FIELDS:
            out[meta_key].pop(field, None)
    return out


def content_hash(doc: dict) -> str:
    """Stable sha256 of the substantive content (see canonical_for_hash)."""
    return hashlib.sha256(strict_dumps(canonical_for_hash(doc)).encode("utf-8")).hexdigest()


def sanitize_text(text, repo_root: str | None = None) -> str:
    """Make an arbitrary message safe for publication: repo-root paths become
    relative, any other absolute path is replaced with a placeholder."""
    if not isinstance(text, str):
        return text
    out = text
    if repo_root:
        out = out.replace(repo_root.rstrip("/") + "/", "").replace(repo_root.rstrip("/"), ".")
    out = re.sub(r"(/Users|/home|/private|/opt|/mount|/var|/tmp)/[^\s\"',;)\]]*", "<path>", out)
    return out
