"""Tests for fantasy/yahoo/cache.py (Part 20/21)."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fantasy.yahoo import cache


class TestCache(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.conn = cache.get_connection(Path(self._tmpdir.name) / "test_cache.db")

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_miss_returns_none(self):
        self.assertIsNone(cache.get_cached(self.conn, "key1"))

    def test_hit_within_ttl_returns_payload(self):
        cache.set_cached(self.conn, "key1", {"a": 1}, ttl_seconds=3600)
        self.assertEqual(cache.get_cached(self.conn, "key1"), {"a": 1})

    def test_expired_entry_returns_none(self):
        cache.set_cached(self.conn, "key1", {"a": 1}, ttl_seconds=-1)  # already expired
        self.assertIsNone(cache.get_cached(self.conn, "key1"))

    def test_set_overwrites_previous_value(self):
        cache.set_cached(self.conn, "key1", {"a": 1}, ttl_seconds=3600)
        cache.set_cached(self.conn, "key1", {"a": 2}, ttl_seconds=3600)
        self.assertEqual(cache.get_cached(self.conn, "key1"), {"a": 2})

    def test_invalidate_removes_entry(self):
        cache.set_cached(self.conn, "key1", {"a": 1}, ttl_seconds=3600)
        cache.invalidate(self.conn, "key1")
        self.assertIsNone(cache.get_cached(self.conn, "key1"))

    def test_cache_age_seconds_none_when_absent(self):
        self.assertIsNone(cache.cache_age_seconds(self.conn, "nope"))

    def test_cache_age_seconds_small_immediately_after_set(self):
        cache.set_cached(self.conn, "key1", {"a": 1}, ttl_seconds=3600)
        age = cache.cache_age_seconds(self.conn, "key1")
        self.assertIsNotNone(age)
        self.assertLess(age, 2.0)


if __name__ == "__main__":
    unittest.main()
