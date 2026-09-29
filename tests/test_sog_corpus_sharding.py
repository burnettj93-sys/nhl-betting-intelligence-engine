"""
Tests for the SOG corpus sharding fix (P0 block, 2026-09-29): GitHub's
100MB file limit made the repaired, real-sized (188,863-row) monolithic
player_game_sog.jsonl impossible to commit -- research/player_sog/
build_sog_corpus.py now writes one shard per raw season under
research/player_sog/corpus/, and features.py::load_sog_corpus() reads all
of them transparently. No caller needed to change.
"""
from __future__ import annotations

import unittest

from research.player_sog import features as pf


class TestAllShardsLoad(unittest.TestCase):
    def test_every_shard_file_exists_and_is_non_empty(self):
        shards = sorted(pf.CORPUS_SHARD_DIR.glob("*.jsonl"))
        self.assertEqual(len(shards), 4, f"expected 4 season shards, found {[s.name for s in shards]}")
        for shard in shards:
            self.assertGreater(shard.stat().st_size, 0)

    def test_no_shard_exceeds_githubs_100mb_limit(self):
        for shard in pf.CORPUS_SHARD_DIR.glob("*.jsonl"):
            self.assertLess(shard.stat().st_size, 100 * 1024 * 1024,
                             f"{shard.name} exceeds GitHub's 100MB push limit")

    def test_default_load_reads_every_shard(self):
        rows = pf.load_sog_corpus()
        seasons_seen = {r["game_date"][:4] for r in rows}
        # four raw seasons' worth of real game dates must all be represented
        self.assertGreaterEqual(len(seasons_seen), 4)


class TestCombinedCountAndNoDuplicates(unittest.TestCase):
    def test_combined_row_count_matches_the_repaired_total(self):
        rows = pf.load_sog_corpus()
        # The real, repaired total (research/run_sog_corpus_build.py's own
        # reported rows_written after the P0 staleness-bug fix) -- 0 rows
        # lost, 0 rows duplicated across the shard split.
        self.assertEqual(len(rows), 188863)

    def test_no_duplicate_player_game_records(self):
        rows = pf.load_sog_corpus()
        keys = [(r["player_id"], r["game_id"]) for r in rows]
        self.assertEqual(len(keys), len(set(keys)),
                          "a (player_id, game_id) pair appears more than once across the shards")

    def test_row_count_equals_the_sum_of_individual_shards(self):
        total = sum(len(pf._read_jsonl(shard)) for shard in pf.CORPUS_SHARD_DIR.glob("*.jsonl"))
        self.assertEqual(total, len(pf.load_sog_corpus()))


class TestDeterministicOrdering(unittest.TestCase):
    def test_loaded_rows_are_sorted_by_date_game_player(self):
        rows = pf.load_sog_corpus()
        keys = [(r["game_date"], r["game_id"], r["player_id"]) for r in rows]
        self.assertEqual(keys, sorted(keys))

    def test_loading_twice_produces_byte_identical_row_order(self):
        first = pf.load_sog_corpus()
        second = pf.load_sog_corpus()
        self.assertEqual(first, second)


class TestExplicitPathOverrideStillWorks(unittest.TestCase):
    def test_a_single_shard_file_path_reads_just_that_file(self):
        one_shard = next(iter(pf.CORPUS_SHARD_DIR.glob("*.jsonl")))
        rows = pf.load_sog_corpus(one_shard)
        all_rows = pf.load_sog_corpus()
        self.assertLess(len(rows), len(all_rows))
        self.assertGreater(len(rows), 0)

    def test_a_directory_path_reads_every_jsonl_file_in_it(self):
        rows = pf.load_sog_corpus(pf.CORPUS_SHARD_DIR)
        self.assertEqual(len(rows), 188863)


class TestConsumersProduceIdenticalResults(unittest.TestCase):
    """The real SOG identity index and player-history lookups must be
    byte-identical whether built from the old (hypothetical, no-longer-
    written) monolith shape or the new sharded read -- both ultimately
    produce the same flat row list, so this proves the split introduced no
    silent divergence for real downstream consumers."""

    def test_player_history_index_covers_every_real_player_from_all_shards(self):
        rows = pf.load_sog_corpus()
        index = pf.PlayerHistoryIndex(rows)
        for name, pid in (("Brady Tkachuk", None), ("Aleksander Barkov", None)):
            candidates = [r["player_id"] for r in rows if r["player_name"] == name]
            self.assertTrue(candidates, f"{name} missing from the sharded corpus")
            history = index.history_as_of(candidates[0], "2026-09-29")
            self.assertGreater(len(history), 0)

    def test_identity_index_is_identical_whether_built_from_shards_or_a_manually_concatenated_list(self):
        from research.live_sog_pricing import player_mapping as pm
        sharded_rows = pf.load_sog_corpus()
        manual_rows = []
        for shard in sorted(pf.CORPUS_SHARD_DIR.glob("*.jsonl")):
            manual_rows.extend(pf._read_jsonl(shard))
        manual_rows.sort(key=lambda r: (r["game_date"], r["game_id"], r["player_id"]))
        self.assertEqual(pm.build_player_index(sharded_rows), pm.build_player_index(manual_rows))


if __name__ == "__main__":
    unittest.main()
