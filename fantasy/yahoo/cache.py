"""
Yahoo compliance rebuild (2026-09-24): this module used to be a
disk-backed (SQLite) cache for Yahoo API RESPONSES -- i.e. Yahoo Fantasy
Information itself. The signed Yahoo API Access and Use Agreement
(2026-09-21), Section 2.c.vii, is unambiguous: "Developer shall not
store, cache or index the Yahoo Fantasy Information." That's exactly
what this module did, even though (confirmed via a real audit before
any change: docs/YAHOO_COMPLIANCE_REBUILD.md) it was never actually
exercised -- `fantasy/storage/fantasy_cache.db` never existed as a file,
and no real Yahoo response was ever written through it.

This module intentionally no longer caches anything, anywhere, ever.
The functions below exist only so any future accidental call fails
loudly and points at this comment, rather than someone quietly
resurrecting a disk-backed Yahoo-response cache. The compliant pattern
(Phase 9/10) is: fetch fresh from Yahoo on every distinct request, hand
the result straight to a deterministic calculation, display it, and let
it fall out of scope -- no cache layer at all, matching
~/yahoo-fantasy-cockpit's own proven, AST-structurally-tested
"no persistence of Yahoo data, anywhere" guarantee (see
tests/test_fantasy_cache.py, which now asserts exactly that instead of
testing cache hits/misses).

Rerun-avoidance within a single Streamlit page view, if ever needed
again, belongs in a plain Python variable scoped to that one function
call -- never a module-level dict, never a file, never a database.
"""
from __future__ import annotations


class YahooCachingProhibited(RuntimeError):
    """Raised by every function below -- see this module's docstring."""


_MESSAGE = ("Caching Yahoo Fantasy Information is prohibited by the signed API "
            "Access and Use Agreement (Section 2.c.vii). Fetch fresh from Yahoo "
            "for each use instead of reaching for a cache.")


def get_connection(*_args, **_kwargs):
    raise YahooCachingProhibited(_MESSAGE)


def get_cached(*_args, **_kwargs):
    raise YahooCachingProhibited(_MESSAGE)


def cache_age_seconds(*_args, **_kwargs):
    raise YahooCachingProhibited(_MESSAGE)


def set_cached(*_args, **_kwargs):
    raise YahooCachingProhibited(_MESSAGE)


def invalidate(*_args, **_kwargs):
    raise YahooCachingProhibited(_MESSAGE)
