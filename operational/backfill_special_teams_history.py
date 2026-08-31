"""
Part 36: one-time backfill of operational/special_teams_history.db from
the ALREADY-LOCAL, already-validated archival corpora
(research/player_sog/player_game_sog.jsonl for PP + total/EV TOI,
research/player_blocks/player_game_blocks.jsonl for PK TOI) -- never
re-fetches the 4-season historical corpus from the NHL TOI reports
(operational/special_teams_toi_report.py is for GOING-FORWARD games
only, past this backfill's coverage).

Run manually:
    python3 -m operational.backfill_special_teams_history
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from operational import special_teams_history_store as sths

SOURCE_TAG = "MONEYPUCK_ARCHIVAL_BACKFILL"


def _load_jsonl(path: Path) -> list[dict]:
    return [json.loads(l) for l in open(path)]


def build_backfill_records() -> list[dict]:
    sog_rows = _load_jsonl(REPO_ROOT / "research" / "player_sog" / "player_game_sog.jsonl")
    blocks_rows = _load_jsonl(REPO_ROOT / "research" / "player_blocks" / "player_game_blocks.jsonl")
    pk_by_key = {(r["player_id"], r["game_id"]): (r.get("pk") or {}).get("icetime_seconds", 0.0)
                 for r in blocks_rows}

    records = []
    for r in sog_rows:
        key = (r["player_id"], r["game_id"])
        total = r["icetime_seconds"]
        pp = (r.get("pp") or {}).get("icetime_seconds", 0.0)
        sh = pk_by_key.get(key, 0.0)
        records.append({
            "game_id": r["game_id"], "player_id": r["player_id"], "game_date": r["game_date"],
            "team": r["team"], "player_name": r.get("player_name"),
            "total_toi_seconds": total, "ev_toi_seconds": max(total - pp - sh, 0.0),
            "pp_toi_seconds": pp, "sh_toi_seconds": sh, "played": total > 0,
            "source": SOURCE_TAG,
        })
    return records


if __name__ == "__main__":
    conn = sths.get_connection()
    records = build_backfill_records()
    n = sths.upsert_records(conn, records)
    summary = sths.coverage_summary(conn)
    print(f"backfilled {n} records")
    print(json.dumps(summary, indent=2))
