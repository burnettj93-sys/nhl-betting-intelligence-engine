"""
LIVE MARKET OBSERVATION LEDGER -- append-only. Records every real SOG
market observation this project chooses to evaluate, prospectively, as
it's captured. This is NOT a bet ledger (Part: "This is NOT a bet
ledger"): an observed opportunity is stored as an OBSERVATION whether or
not the user actually placed a real wager on it. Isolated from nhl.db
entirely -- its own file, same isolation precedent as every other
research artifact in this project.

Append-only means exactly that: a correction is a NEW row (with a new
observation_id and its own observed_at_utc), never an edit or delete of
an existing line -- mirrors the bitemporal append-only discipline
documented for nhl.db's own event tables, applied here to a plain JSONL
file since this data has nothing to do with the PIT-safe training
pipeline.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
LEDGER_PATH = REPO_ROOT / "research" / "live_sog_pricing" / "observation_ledger.jsonl"

REQUIRED_FIELDS = [
    "observation_id", "observed_at_utc", "nhl_game_id", "provider_event_id", "player_id", "player",
    "market", "point", "side", "draftkings_price", "market_raw_probability", "market_no_vig_probability",
    "model_probability", "conservative_probability", "fair_price", "conservative_fair_price",
    "raw_edge", "conservative_edge", "raw_ev", "conservative_ev", "confidence", "lineup_status",
    "decision", "source_raw_payload_sha256",
]


def make_observation_id(observed_at_utc: str, provider_event_id: str, player_id: str,
                         market: str, side: str, point) -> str:
    """Deterministic (not random) id from the observation's own content
    -- lets idempotency/dedupe checks work without a database, and makes
    two independent runs that observe the exact same real quote at the
    exact same real timestamp produce the exact same id rather than two
    duplicate rows."""
    basis = f"{observed_at_utc}|{provider_event_id}|{player_id}|{market}|{side}|{point}"
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:24]


def append_observation(observation: dict, path: Path = LEDGER_PATH) -> bool:
    """Returns True if a new row was appended, False if an observation
    with the identical observation_id already exists in the ledger
    (idempotent re-run protection -- Part 32's "identical observation
    idempotency/deduping")."""
    missing = [f for f in REQUIRED_FIELDS if f not in observation]
    if missing:
        raise ValueError(f"observation missing required fields: {missing}")

    existing_ids = load_observation_ids(path)
    if observation["observation_id"] in existing_ids:
        return False

    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as f:
        f.write(json.dumps(observation, sort_keys=True) + "\n")
    return True


def load_observation_ids(path: Path = LEDGER_PATH) -> set[str]:
    if not path.exists():
        return set()
    ids = set()
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                ids.add(json.loads(line)["observation_id"])
    return ids


def load_all_observations(path: Path = LEDGER_PATH) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows
