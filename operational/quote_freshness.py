"""
Quote freshness: how old is the bookmaker's own price, not merely when we fetched it.

Two timestamps are kept apart everywhere a price is used:
  * retrieved_at  - when this system pulled the response (an archive/receipt time);
  * quote_updated - the provider's `last_update` for that bookmaker market (when the price was last set).
A response fetched a minute ago can contain a market last updated six days ago; only the second timestamp says
how old the price is.

Policy (one function, used by every candidate source):
  * the quote timestamp must exist and parse (ISO-8601; a missing zone means UTC);
  * it must not be in the future: later than the retrieval time (or, with no retrieval time, `now`) by more than
    FUTURE_TOLERANCE_S seconds is rejected, and a timestamp inside that tolerance counts as age 0;
  * the quote must be no older than the caller's limit (minutes);
  * if a retrieval time is supplied it must parse and also be within the limit.
Anything else is NOT fresh, with a specific reason. Nothing here guesses a timestamp.
"""
from __future__ import annotations

import datetime as dt

FUTURE_TOLERANCE_S = 60.0

FRESH = "FRESH"
MISSING_QUOTE_TIMESTAMP = "MISSING_QUOTE_TIMESTAMP"
MALFORMED_QUOTE_TIMESTAMP = "MALFORMED_QUOTE_TIMESTAMP"
FUTURE_QUOTE_TIMESTAMP = "FUTURE_QUOTE_TIMESTAMP"
STALE_QUOTE = "STALE_QUOTE"
MISSING_RETRIEVAL_TIMESTAMP = "MISSING_RETRIEVAL_TIMESTAMP"
MALFORMED_RETRIEVAL_TIMESTAMP = "MALFORMED_RETRIEVAL_TIMESTAMP"
STALE_RETRIEVAL = "STALE_RETRIEVAL"


def parse_utc(value) -> dt.datetime | None:
    """A timezone-aware UTC datetime, or None when the value is missing or not a valid ISO-8601 string."""
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        parsed = dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def iso_z(value: dt.datetime | None) -> str | None:
    return None if value is None else value.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def assess(quote_updated, retrieved_at, now: dt.datetime, limit_min: float, *, require_retrieval: bool = True) -> dict:
    """Returns {"fresh", "status", "quote_updated_utc", "retrieved_at_utc", "quote_age_min",
    "retrieval_age_min", "limit_min"}. `now` must be timezone-aware."""
    if now.tzinfo is None:                      # callers in this codebase sometimes hold naive-UTC clocks
        now = now.replace(tzinfo=dt.timezone.utc)
    out = {"fresh": False, "status": None, "quote_updated_utc": None, "retrieved_at_utc": None,
           "quote_age_min": None, "retrieval_age_min": None, "limit_min": limit_min}
    if quote_updated is None or (isinstance(quote_updated, str) and not quote_updated.strip()):
        out["status"] = MISSING_QUOTE_TIMESTAMP
        return out
    quote = parse_utc(quote_updated)
    if quote is None:
        out["status"] = MALFORMED_QUOTE_TIMESTAMP
        return out
    out["quote_updated_utc"] = iso_z(quote)

    retrieved = None
    if retrieved_at is None or (isinstance(retrieved_at, str) and not retrieved_at.strip()):
        if require_retrieval:
            out["status"] = MISSING_RETRIEVAL_TIMESTAMP
            return out
    else:
        retrieved = parse_utc(retrieved_at)
        if retrieved is None:
            out["status"] = MALFORMED_RETRIEVAL_TIMESTAMP
            return out
        out["retrieved_at_utc"] = iso_z(retrieved)
        if (retrieved - now).total_seconds() > FUTURE_TOLERANCE_S:
            out["status"] = FUTURE_QUOTE_TIMESTAMP     # a retrieval time in the future is as unusable as a future quote
            return out

    reference = retrieved or now
    if (quote - reference).total_seconds() > FUTURE_TOLERANCE_S or (quote - now).total_seconds() > FUTURE_TOLERANCE_S:
        out["status"] = FUTURE_QUOTE_TIMESTAMP
        return out
    quote_age = max((now - quote).total_seconds(), 0.0) / 60.0          # compare unrounded; round only for display
    out["quote_age_min"] = round(quote_age, 1)
    retrieval_age = None
    if retrieved is not None:
        retrieval_age = max((now - retrieved).total_seconds(), 0.0) / 60.0
        out["retrieval_age_min"] = round(retrieval_age, 1)
    if quote_age > limit_min:
        out["status"] = STALE_QUOTE
        return out
    if retrieval_age is not None and retrieval_age > limit_min:
        out["status"] = STALE_RETRIEVAL
        return out
    out["fresh"] = True
    out["status"] = FRESH
    return out
