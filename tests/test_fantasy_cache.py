"""Yahoo compliance rebuild (2026-09-24): fantasy/yahoo/cache.py used to
be a real disk-backed cache for Yahoo API responses -- prohibited by the
signed API agreement's Section 2.c.vii ("shall not store, cache or index
the Yahoo Fantasy Information"). These tests now prove the opposite of
what they used to: every function raises immediately, and none of them
ever touches disk."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fantasy.yahoo import cache


class TestCachingIsProhibited(unittest.TestCase):
    def test_get_connection_raises(self):
        with self.assertRaises(cache.YahooCachingProhibited):
            cache.get_connection()

    def test_get_cached_raises(self):
        with self.assertRaises(cache.YahooCachingProhibited):
            cache.get_cached(None, "key1")

    def test_set_cached_raises(self):
        with self.assertRaises(cache.YahooCachingProhibited):
            cache.set_cached(None, "key1", {"a": 1}, ttl_seconds=3600)

    def test_cache_age_seconds_raises(self):
        with self.assertRaises(cache.YahooCachingProhibited):
            cache.cache_age_seconds(None, "key1")

    def test_invalidate_raises(self):
        with self.assertRaises(cache.YahooCachingProhibited):
            cache.invalidate(None, "key1")

    def test_no_cache_file_is_ever_created(self):
        with tempfile.TemporaryDirectory() as tmp:
            candidate = Path(tmp) / "fantasy_cache.db"
            with self.assertRaises(cache.YahooCachingProhibited):
                cache.get_connection(candidate)
            self.assertFalse(candidate.exists())


if __name__ == "__main__":
    unittest.main()
