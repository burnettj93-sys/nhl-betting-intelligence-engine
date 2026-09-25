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


def canonical_for_hash(doc: dict) -> dict:
    """The document with publication-only metadata and compute-time stamps removed."""
    out = _strip_volatile(json.loads(strict_dumps(doc)))
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
