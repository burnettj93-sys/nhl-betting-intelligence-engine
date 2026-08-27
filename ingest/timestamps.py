"""
v2.1.1 spec item 6: a single, canonical UTC timestamp representation for
every value this system persists, applied BEFORE the live NHL API pull is
ever attempted.

The problem: point-in-time eligibility (features/point_in_time.py) relies
entirely on ordering and equality comparisons between timestamp strings --
`observed_at_utc <= prediction_time_utc`, `result_observed_at_utc <
prediction_time_utc`, and so on -- some done in SQL (lexicographic string
comparison), some done in Python after `dt.datetime.fromisoformat()`.
Both comparison styles silently give WRONG answers if the strings being
compared don't share one representation: `"2026-10-01T19:00:00-04:00"`
and `"2026-10-01T23:00:00Z"` denote the exact same instant but sort
differently as strings, and a real NHL API response mixes `Z`-suffixed
timestamps with whatever a manually-fed news/roster source happens to
supply (naive, offset, or otherwise). Real ingestion must never begin
until every timestamp entering the system has gone through one place.

Canonical representation (documented, single form): a NAIVE ISO-8601
string (`YYYY-MM-DDTHH:MM:SS[.ffffff]`, no `Z`, no UTC offset) that is
BY CONVENTION always UTC -- exactly the form this codebase has used
everywhere since v1 (see tests/helpers.py::t(), every timestamp already
stored via demo/test fixtures, and every point-in-time comparison in
features/point_in_time.py). This module doesn't change that convention;
it makes it explicit, documented, and -- critically -- actually ENFORCED
at every real ingestion entry point, instead of silently assumed.
`normalize_utc_timestamp()` accepts any of the following incoming forms
and converts each to that one canonical string:
  - naive, already-UTC-by-convention:  "2026-10-01T23:00:00"
  - Z-suffixed UTC:                    "2026-10-01T23:00:00Z"
  - explicit UTC offset:               "2026-10-01T23:00:00+00:00"
  - any other explicit offset:         "2026-10-01T19:00:00-04:00"
All four of the above are the same instant and normalize to the identical
canonical string "2026-10-01T23:00:00" -- see
tests/test_timestamp_normalization.py.

Every ingest/nhl_api.py write path normalizes its incoming
observed_at_utc/effective_at_utc/scheduled_start_utc-shaped parameters
through this function before they ever reach a SQL INSERT/UPDATE, so a
mixture of representations can never enter the database in the first
place.
"""
from __future__ import annotations

import datetime as dt


class UnsupportedTimestampError(ValueError):
    """Raised when a value can't be parsed as any supported timestamp
    form -- fail loudly rather than silently persisting a bad string that
    would later corrupt a point-in-time comparison."""


def normalize_utc_timestamp(raw: str | None) -> str | None:
    """Parses `raw` as a timezone-aware instant (treating a naive input as
    already-UTC, this codebase's documented convention -- see module
    docstring), converts it to UTC, and returns the single canonical naive
    ISO-8601 string representation. Idempotent: normalizing an
    already-canonical string returns it unchanged.

    Passing None returns None unchanged -- several callers pass an
    optional timestamp (e.g. record_roster_status's expected_return_at)
    that may legitimately be absent; that is not a malformed timestamp
    and should not raise."""
    if raw is None:
        return None
    if not isinstance(raw, str) or not raw.strip():
        raise UnsupportedTimestampError(f"unsupported timestamp form: {raw!r}")
    s = raw.strip()
    # dt.datetime.fromisoformat() only accepts a bare "Z" suffix as of
    # Python 3.11 -- translate it to the equivalent explicit "+00:00"
    # offset first so this works on earlier interpreters too.
    if s[-1] in ("Z", "z"):
        s = s[:-1] + "+00:00"
    try:
        parsed = dt.datetime.fromisoformat(s)
    except ValueError as exc:
        raise UnsupportedTimestampError(f"unsupported timestamp form: {raw!r}") from exc
    if parsed.tzinfo is None:
        # naive input -- by this codebase's documented convention, a
        # naive timestamp IS already a UTC instant; attach UTC explicitly
        # rather than guessing at some other zone.
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    else:
        parsed = parsed.astimezone(dt.timezone.utc)
    return parsed.replace(tzinfo=None).isoformat()
