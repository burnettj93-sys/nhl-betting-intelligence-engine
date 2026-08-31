"""
PIT-safe access over the joint scoring corpus itself -- used for the
shrunk conversion-rate models (Goal|SOG, Point|SOG, Assist|SOG), which
need matched (SOG, Goals, Assists, Points) tuples from the SAME games,
already linked by build_joint_scoring_corpus.py.
"""
from __future__ import annotations

import bisect
import json
from collections import defaultdict
from pathlib import Path

CORPUS_PATH = Path(__file__).resolve().parent / "joint_scoring.jsonl"


def load_joint_scoring_corpus(path: str | Path = CORPUS_PATH) -> list[dict]:
    rows = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    rows.sort(key=lambda r: (r["game_date"], r["game_id"], r["player_id"]))
    return rows


class JointScoringHistoryIndex:
    def __init__(self, rows: list[dict]):
        by_player = defaultdict(list)
        for r in rows:
            by_player[r["player_id"]].append(r)
        self._by_player: dict[str, tuple[list[str], list[dict]]] = {}
        for player_id, prows in by_player.items():
            ordered = sorted(prows, key=lambda r: (r["game_date"], r["game_id"]))
            self._by_player[player_id] = ([r["game_date"] for r in ordered], ordered)

    def history_as_of(self, player_id: str, prediction_game_date: str) -> list[dict]:
        entry = self._by_player.get(player_id)
        if entry is None:
            return []
        dates, ordered = entry
        cut = bisect.bisect_left(dates, prediction_game_date)
        return ordered[:cut]
