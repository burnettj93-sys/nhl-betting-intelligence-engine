"""POSTMORTEM_LEARNING_14D: a temporary, PAPER-ONLY learning cohort (owner authorisation, 2026-10-20).

For 14 Eastern days (2026-10-20 through 2026-11-02 inclusive, America/Toronto dates) the paper book targets 10 paper bets a day at $10 each, on real current markets priced -150 or better, to build enough live
observations to learn where the model is right and wrong. It is NOT a betting recommendation and NOT Eggy: Eggy's Parlay Pick of the Day stays one official pick a day, on its own ledger and rules, and an Eggy
leg that also appears here is only labelled (the instruments differ: a parlay there, a single here, and the ledgers are separate, so nothing is ever staked twice on the same ticket).

What is different from the normal paper book, and only that: more bets per day, no flagship conviction bar, a price floor of -150, singles allowed. What is NOT different, ever: every integrity gate. A bet is recorded
only on a REAL DraftKings price (the last one this system observed, with a valid time) for a real, not-yet-started game, with a verified identity, a market the engine has a certified contract, a model estimate and a
settlement path for, once (no duplicate), at that actual price. The price's AGE is not a gate here (owner policy 2026-10-21: accuracy over arbitrary freshness): it is recorded with every bet (capture time, age at
recording, price status) so the postmortem can learn empirically whether older pregame quotes degrade results. Edges are always "model edge at the last observed price", never a current execution price. It never fabricates a bet to reach 10: if fewer legitimate bets exist the day records fewer and says exactly why.

Accounting (a separate, clearly labelled ledger, `learning_cohort.db`): its own experiment bankroll of $500.00. Whenever the bankroll cannot fund the next $10 bet it is reset to $500.00 by a
funding event that is logged (time, balance before, amount, balance after, experiment day, reason LEARNING_EXPERIMENT_BANKROLL_RESET) and is never profit or returned stake: betting P/L, ROI, hit rate and calibration come
from settled ticket results only. Historical results are never rewritten. Nothing here touches Eggy's ledger, the model book (Experiment 2) or any other ledger.

Automatic end: on 2026-11-03 (Eastern) the experiment is over by date arithmetic alone, with no manual step: nothing new is recorded, the reset rule stops, open bets still settle. The normal paper-book policy was
never changed and simply continues.

Ranking (a transparent weighted score, not a recommendation): conservative probability 0.30, model probability 0.15, model edge at the last observed price 0.25, calibration confidence (how much evidence stands
behind the estimate) 0.15, model quality 0.15; price age carries no weight. Diversification is a soft penalty while the day's bets are chosen (same game, same player, same market, same price band); a strong bet is never rejected only for sharing
a game. Slots are released through the day with the puck-drop waves so early prices cannot use up all ten.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import hashlib
import json
import math
import sqlite3
from collections import Counter
from types import SimpleNamespace

from operational import eastern_time as et
from operational import state_paths

COHORT = "POSTMORTEM_LEARNING_14D"
EXPERIMENT_ID = "LEARN-14D-2026-10-20"
START_DATE, END_DATE = "2026-10-20", "2026-11-02"
DAILY_TARGET = 10
STAKE = 10.0
ODDS_FLOOR = -150.0                  # an American price of -150 or longer (a price MORE expensive than -150, e.g. -155, is excluded)
INITIAL_BANKROLL = 500.0             # the owner's instruction (2026-10-20): start at $500 and reset to $500 whenever cash cannot fund the next bet
RESET_TO = 500.0                     # cash is reset TO this amount whenever it cannot fund the next bet
RESET_REASON = "LEARNING_EXPERIMENT_BANKROLL_RESET"
DB_NAME = "learning_cohort.db"
STATE_NAME = "learning_experiment_state.json"
STATUS_NAME = "learning_experiment_status.json"
LOG_NAME = "learning_experiment_activation_log.jsonl"
RELAX_MIN = 25          # in the last 25 minutes before a wave's first puck drop its prices can no longer be used afterwards, so only a small reserve is held for later waves
RELAXED_RESERVE_MAX = 2
WAVE_SPAN_MIN = 90
CODE_VERSION = "learning-cohort-2026-10-21.3-price-age-advisory"
SELECTION_WEIGHTS = {"conservative_probability": 0.30, "model_probability": 0.15, "edge": 0.25, "calibration_confidence": 0.15, "freshness": 0.0, "model_quality": 0.15}          # price age is recorded, never ranked (owner policy 2026-10-21)
PRICE_AGE_STATUS = ((5.0, "CURRENT"), (30.0, "RECENT"), (120.0, "LAST_KNOWN_PREGAME"))          # by minutes since this system last observed the price; older is VERY_OLD_PREGAME
PRICE_AGE_BUCKETS = ((5.0, "0-5 min"), (15.0, "6-15 min"), (30.0, "16-30 min"), (60.0, "31-60 min"), (120.0, "61-120 min"))          # then "120+ min"
OLD_BUT_REAL_STATUSES = ("FRESH", "OK", "STALE_QUOTE", "STALE_RETRIEVAL", "")          # a quote that is merely old; a missing, malformed or future time is never in this list
# A ranking prior for how much evidence stands behind each market's estimate. It is a judgement written down and fixed in the policy digest, NOT a measurement: shots legs carry the player's own record
# held to the model (validated on 2025, anchor.py); the others use the calibrated model's own probability, which has no capped record-based estimate; a moneyline is the market's own no-vig chance (no model
# here beats the market). One purpose of this experiment is to measure all of them.
MODEL_QUALITY = {"PLAYER_SOG_ALTERNATE": 1.0, "PLAYER_SOG": 1.0, "GOALIE_SAVES": 0.7, "PLAYER_POINTS": 0.6, "PLAYER_GOALS": 0.5, "MONEYLINE": 0.5}
EDGE_POSITIVE, EDGE_NEUTRAL, EDGE_NEGATIVE = "POSITIVE_EDGE", "NEUTRAL_UNCERTAIN_EDGE", "LEARNING_SAMPLE_NEGATIVE_EDGE"
EDGE_CLASSES = (EDGE_POSITIVE, EDGE_NEUTRAL, EDGE_NEGATIVE)
EDGE_BAND = 0.02                       # estimated edge (the recorded hit chance less the price's implied chance) of +2 points or more is positive; within 2 points either way is neutral; below -2 is negative
ANCHORED_BASES = ("ANCHORED_RECORD_ESTIMATE", "MARKET_NO_VIG")
UNANCHORED_SHOTS_FACTOR = 0.7          # a shots leg whose record is thin or below the flagship floor still ranks, with this share of the shots model-quality prior
DIVERSIFICATION_PENALTY = {"same_game": 0.03, "same_player": 0.10, "same_market_family_over_4": 0.02, "same_price_band": 0.01, "same_team": 0.01}
PRICE_BANDS = ((-150, -131, "-150 to -131"), (-130, -111, "-130 to -111"), (-110, 100, "-110 to +100"), (101, 150, "+101 to +150"), (151, 10 ** 6, "+151 and longer"))
ELIGIBLE_FAMILIES = ("MONEYLINE", "PLAYER_SOG_ALTERNATE", "PLAYER_SOG", "PLAYER_POINTS", "PLAYER_GOALS", "GOALIE_SAVES")

POLICY = {
    "policy_id": "postmortem-learning-14d-2026-10-20", "cohort": COHORT, "experiment_id": EXPERIMENT_ID, "timezone": "America/Toronto",
    "start_date": START_DATE, "end_date": END_DATE, "days": 14, "daily_target": DAILY_TARGET, "stake_usd": STAKE, "max_daily_exposure_usd": DAILY_TARGET * STAKE,
    "odds_floor_american": ODDS_FLOOR, "paper_only": True, "real_money": "NOT authorised",
    "eggy": {"official_picks_per_day": 1, "changed": False}, "initial_bankroll_usd": INITIAL_BANKROLL, "reset_to_usd": RESET_TO, "reset_when_below_usd": STAKE, "reset_reason": RESET_REASON,
    "eligible_market_families": list(ELIGIBLE_FAMILIES), "selection_weights": SELECTION_WEIGHTS, "model_quality": MODEL_QUALITY, "diversification_penalty": DIVERSIFICATION_PENALTY,
    "slot_release": "cumulative cap ceil(10 x games through the wave / games today), by puck-drop wave; within the last 25 minutes before a wave the cap relaxes to 10 less one reserved slot per later wave (at most two)",
    "behaviour": {"relax_min": RELAX_MIN, "relaxed_reserve_max": RELAXED_RESERVE_MAX, "wave_span_min": WAVE_SPAN_MIN, "unanchored_shots_factor": UNANCHORED_SHOTS_FACTOR, "price_bands": [b[2] for b in PRICE_BANDS],
                  "code_version": CODE_VERSION,
                  "probability_basis": "shots: the book's anchored record estimate (record held to the model + 0.02, 70% for 3+) when it exists, else the model probability; moneyline: the market's no-vig chance; points, goals and saves: the "
                                       "calibrated model probability, labelled unanchored; the recorded conservative probability is never above the source leg's own",
                  "exclusions": "one side of a game's moneyline; one threshold per player and statistic (PLAYER_SOG and PLAYER_SOG_ALTERNATE are the same statistic); the engine's leg gate (leg_is_eligible) applies unchanged",
                  "honours_owner_recording_pause": True,
                  "edge_classes": {"POSITIVE_EDGE": f"edge >= +{EDGE_BAND} on an anchored estimate", "NEUTRAL_UNCERTAIN_EDGE": f"edge within {EDGE_BAND} of zero, or positive on an unanchored estimate",
                                   "LEARNING_SAMPLE_NEGATIVE_EDGE": f"edge < -{EDGE_BAND}"},
                  "negative_edge": "a LEARNING_SAMPLE_NEGATIVE_EDGE candidate passes every integrity and support gate like any other, is recorded only to fill the day's sample after positive and neutral candidates, "
                                   "and only once no better price for its wave can still arrive (the final 25 minutes before the wave's puck drop); it is tagged at recording time and reported separately"},
    "integrity_gates": ["real current event", "real sportsbook market with a verified contract", "a real DraftKings price observation with a valid, non-future time (its AGE is recorded, never a gate)",
                        "verified identity on a current official roster", "event not started", "supported settlement path", "no duplicate bet", "model estimate present and eligible", "no demo or simulated data",
                        "no fabricated or approximated price", "market not withdrawn (a price is only used from the newest capture of its market)"],
    "price_age_policy": {"gate": False, "recorded": ["price_capture_timestamp", "price_provider_update_utc", "price_age_at_recording_min", "provider_quote_age_at_recording_min", "price_status", "price_age_bucket"],
                         "statuses": "CURRENT <=5 min, RECENT <=30, LAST_KNOWN_PREGAME <=120, VERY_OLD_PREGAME beyond (minutes since this system last observed the price)",
                         "buckets": [b[1] for b in PRICE_AGE_BUCKETS] + ["120+ min"], "ranking": "price age carries no ranking weight",
                         "edge_label": "MODEL EDGE AT LAST OBSERVED PRICE (never presented as a current execution price)", "eggy": "unchanged: Eggy keeps its strict freshness limits"},
    "ends": "automatically on 2026-11-03 Eastern (date arithmetic; no manual step)",
}


def policy_digest() -> str:
    return hashlib.sha256(json.dumps(POLICY, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:16]


# --------------------------------------------------------------------------------------------------- phase
def phase(now: dt.datetime | None = None) -> dict:
    """NOT_STARTED / ACTIVE / ENDED from the Eastern date alone, plus the day number (1..14)."""
    today = et.eastern_today(now or dt.datetime.now(dt.timezone.utc))
    d0, d1, d = dt.date.fromisoformat(START_DATE), dt.date.fromisoformat(END_DATE), dt.date.fromisoformat(today)
    if d < d0:
        return {"phase": "NOT_STARTED", "day": 0, "today": today}
    if d > d1:
        return {"phase": "ENDED", "day": 14, "today": today}
    return {"phase": "ACTIVE", "day": (d - d0).days + 1, "today": today}


def activation() -> dict | None:
    try:
        return json.loads(state_paths.path(STATE_NAME).read_text())
    except (OSError, ValueError):
        return None


def is_authorised() -> bool:
    """The owner's approval of THIS exact policy (its digest). A policy edited after approval is not authorised until re-approved: fail closed."""
    a = activation()
    return bool(a and a.get("approved") and a.get("policy_digest") == policy_digest())


def is_recording(now: dt.datetime | None = None) -> bool:
    return is_authorised() and phase(now)["phase"] == "ACTIVE"


# --------------------------------------------------------------------------------------------------- ledger
SCHEMA = """
CREATE TABLE IF NOT EXISTS bets (
    bet_id TEXT PRIMARY KEY, idempotency_key TEXT NOT NULL UNIQUE, experiment_id TEXT NOT NULL, cohort TEXT NOT NULL, policy_digest TEXT NOT NULL,
    et_date TEXT NOT NULL, experiment_day INTEGER NOT NULL, selection_rank INTEGER NOT NULL, wave_index INTEGER, created_at_utc TEXT NOT NULL, created_at_et TEXT NOT NULL,
    game_id TEXT NOT NULL, event_id TEXT, event_start_utc TEXT, market_family TEXT NOT NULL, settlement_market_id TEXT NOT NULL, threshold INTEGER, side TEXT NOT NULL,
    participant_id TEXT NOT NULL, participant_name TEXT, team TEXT, opponent TEXT, american_price REAL NOT NULL, decimal_price REAL NOT NULL, stake REAL NOT NULL,
    model_probability REAL, conservative_probability REAL NOT NULL, implied_probability REAL NOT NULL, edge REAL NOT NULL, ev REAL NOT NULL,
    selection_score REAL NOT NULL, score_components_json TEXT NOT NULL, selection_reason TEXT NOT NULL, freshness_status TEXT, quote_updated_utc TEXT, retrieved_at_utc TEXT, quote_age_min REAL,
    model_version TEXT, price_source TEXT NOT NULL, dependence_json TEXT, eggy_relation TEXT, leg_json TEXT NOT NULL, probability_basis TEXT, source_conservative_probability REAL, edge_class TEXT NOT NULL, edge_class_reason TEXT,
    price_capture_timestamp TEXT, price_provider_update_utc TEXT, price_age_at_recording_min REAL, provider_quote_age_at_recording_min REAL, price_status TEXT, price_age_bucket TEXT,
    result_status TEXT NOT NULL DEFAULT 'PENDING', settled_at_utc TEXT, settled_odds REAL, profit_loss REAL, return_amount REAL, closing_odds REAL, clv REAL, notes TEXT, settlement_json TEXT
);
CREATE TABLE IF NOT EXISTS funding_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, at_utc TEXT NOT NULL, at_et TEXT NOT NULL, kind TEXT NOT NULL, balance_before REAL NOT NULL, amount REAL NOT NULL, balance_after REAL NOT NULL,
    experiment_day INTEGER, reason TEXT NOT NULL, note TEXT
);
CREATE TABLE IF NOT EXISTS daily_records (
    et_date TEXT PRIMARY KEY, experiment_day INTEGER NOT NULL, target INTEGER NOT NULL, recorded INTEGER NOT NULL, eligible_now INTEGER, slots_open INTEGER, shortfall_json TEXT, updated_at_utc TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS one_initial_funding ON funding_events (kind) WHERE kind = 'INITIAL';
CREATE TRIGGER IF NOT EXISTS bets_never_replaced BEFORE INSERT ON bets
WHEN EXISTS (SELECT 1 FROM bets WHERE bet_id = NEW.bet_id OR idempotency_key = NEW.idempotency_key)
BEGIN SELECT RAISE(ABORT, 'a learning-cohort bet already exists; it cannot be replaced'); END;
CREATE TRIGGER IF NOT EXISTS bets_entry_is_immutable BEFORE UPDATE ON bets
WHEN @ENTRY_CHANGED@ OR (OLD.result_status IN ('WIN','LOSS','VOID') AND (@RESULT_CHANGED@))
BEGIN SELECT RAISE(ABORT, 'a recorded learning-cohort bet cannot be edited; only an open bet gets its result, once'); END;
CREATE TRIGGER IF NOT EXISTS funding_is_append_only_u BEFORE UPDATE ON funding_events BEGIN SELECT RAISE(ABORT, 'funding events are append-only'); END;
CREATE TRIGGER IF NOT EXISTS funding_is_append_only_d BEFORE DELETE ON funding_events BEGIN SELECT RAISE(ABORT, 'funding events are append-only'); END;
CREATE TRIGGER IF NOT EXISTS bets_are_never_deleted BEFORE DELETE ON bets BEGIN SELECT RAISE(ABORT, 'learning-cohort bets are never deleted'); END;
"""


ENTRY_COLUMNS = ("bet_id", "idempotency_key", "experiment_id", "cohort", "policy_digest", "et_date", "experiment_day", "selection_rank", "wave_index", "created_at_utc", "created_at_et", "game_id", "event_id",
                 "event_start_utc", "market_family", "settlement_market_id", "threshold", "side", "participant_id", "participant_name", "team", "opponent", "american_price", "decimal_price", "stake",
                 "model_probability", "conservative_probability", "implied_probability", "edge", "ev", "selection_score", "score_components_json", "selection_reason", "freshness_status", "quote_updated_utc",
                 "retrieved_at_utc", "quote_age_min", "model_version", "price_source", "dependence_json", "eggy_relation", "leg_json", "probability_basis", "source_conservative_probability", "edge_class", "edge_class_reason",
                 "price_capture_timestamp", "price_provider_update_utc", "price_age_at_recording_min", "provider_quote_age_at_recording_min", "price_status", "price_age_bucket")
PRICE_COLUMNS = (("price_capture_timestamp", "TEXT"), ("price_provider_update_utc", "TEXT"), ("price_age_at_recording_min", "REAL"), ("provider_quote_age_at_recording_min", "REAL"),
                 ("price_status", "TEXT"), ("price_age_bucket", "TEXT"))
RESULT_COLUMNS = ("result_status", "settled_at_utc", "settled_odds", "profit_loss", "return_amount", "closing_odds", "clv", "notes", "settlement_json")
SCHEMA = (SCHEMA.replace("@ENTRY_CHANGED@", " OR ".join(f"OLD.{c} IS NOT NEW.{c}" for c in ENTRY_COLUMNS))
          .replace("@RESULT_CHANGED@", " OR ".join(f"OLD.{c} IS NOT NEW.{c}" for c in RESULT_COLUMNS)))


def open_ledger(path) -> sqlite3.Connection:
    """The ledger connection: rows by name, the triggers installed, and recursive triggers ON so that even INSERT OR REPLACE cannot silently replace a bet."""
    conn = sqlite3.connect(str(path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA recursive_triggers = ON")
    _migrate(conn)
    conn.executescript(SCHEMA)
    return conn


def _parse(value):
    from operational import quote_freshness
    return quote_freshness.parse_utc(value)


def _observed_at(leg) -> dt.datetime:
    """When this system last observed the leg's price (retrieval time, else the provider's quote time, else the capture time); the earliest instant when unknown."""
    return _parse(leg.retrieved_at_utc) or _parse(leg.quote_updated_utc) or _parse(leg.captured_at_utc) or dt.datetime.min.replace(tzinfo=dt.timezone.utc)


def price_age_status(age_min: float | None) -> str | None:
    if age_min is None:
        return None
    for limit, name in PRICE_AGE_STATUS:
        if age_min <= limit:
            return name
    return "VERY_OLD_PREGAME"


def price_age_bucket(age_min: float | None) -> str:
    if age_min is None:
        return "unknown"
    for limit, name in PRICE_AGE_BUCKETS:
        if age_min <= limit:
            return name
    return "120+ min"


def price_observation(retrieved_utc, provider_utc, now: dt.datetime) -> dict | None:
    """The real price observation behind a leg: when this system last SAW the price (the capture it was read from) and the provider's own last-update time for it, and their ages at `now`.
    None when no real observation time can be established (a missing or malformed time), or when it lies in the future: those are accuracy problems, not age. The age itself never disqualifies."""
    if retrieved_utc and _parse(retrieved_utc) is None:
        return None                                  # a retrieval time that is present but unreadable is corrupt data, not a reason to fall back to another time
    obs, prov = _parse(retrieved_utc) or _parse(provider_utc), _parse(provider_utc)
    if obs is None or (obs - now).total_seconds() > 60.0:
        return None
    if prov is not None and (prov - now).total_seconds() > 60.0:
        return None
    age = round(max((now - obs).total_seconds(), 0.0) / 60.0, 1)          # status and bucket come from the same rounded number that is stored
    prov_age = None if prov is None else round(max((now - prov).total_seconds(), 0.0) / 60.0, 1)
    return {"captured_utc": obs.strftime("%Y-%m-%dT%H:%M:%SZ"), "provider_update_utc": None if prov is None else prov.strftime("%Y-%m-%dT%H:%M:%SZ"), "age_min": age,
            "provider_age_min": prov_age, "status": price_age_status(age), "bucket": price_age_bucket(age)}


def _migrate(conn) -> None:
    """An existing ledger (the Day-1 bets recorded before the price-age metadata existed) gains the new columns. Their values are derived deterministically from the entry's own frozen leg and its own recording
    time, so nothing about what was recorded changes; the entry-immutability trigger is dropped for this one derivation and recreated straight after. One transaction: a crash leaves either the old ledger or the
    complete migration, and the backfill is keyed on the missing values, not on the missing columns, so a half-finished earlier attempt is completed on the next open."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(bets)")}
    if not cols:
        return
    missing = [(n, t) for n, t in PRICE_COLUMNS if n not in cols]
    if not missing and conn.execute("SELECT 1 FROM bets WHERE price_status IS NULL LIMIT 1").fetchone() is None:
        return                                                      # nothing to do: no write lock is taken on an ordinary open
    prior = conn.isolation_level
    conn.isolation_level = None
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            for n, t in missing:
                conn.execute(f"ALTER TABLE bets ADD COLUMN {n} {t}")
            todo = conn.execute("SELECT bet_id, created_at_utc, retrieved_at_utc, quote_updated_utc FROM bets WHERE price_status IS NULL").fetchall()
            if todo:
                conn.execute("DROP TRIGGER IF EXISTS bets_entry_is_immutable")
                for r in todo:
                    created = _parse(r["created_at_utc"])
                    info = price_observation(r["retrieved_at_utc"], r["quote_updated_utc"], created) if created else None
                    if info:
                        conn.execute("UPDATE bets SET price_capture_timestamp=?, price_provider_update_utc=?, price_age_at_recording_min=?, provider_quote_age_at_recording_min=?, price_status=?, price_age_bucket=? WHERE bet_id=?",
                                     (info["captured_utc"], info["provider_update_utc"], info["age_min"], info["provider_age_min"], info["status"], info["bucket"], r["bet_id"]))
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.isolation_level = prior


def connect(path=None) -> sqlite3.Connection:
    p = path or state_paths.path(DB_NAME)
    if str(p) != ":memory:":
        p.parent.mkdir(parents=True, exist_ok=True)
    return open_ledger(p)


def _utc(now: dt.datetime) -> str:
    return now.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _et(now: dt.datetime) -> str:
    return now.astimezone(et.EASTERN).strftime("%Y-%m-%d %H:%M:%S %Z")


def balance(conn) -> dict:
    """Bankroll and performance, from the ledger. Funding events are never performance: betting P/L comes from settled tickets only."""
    fund = conn.execute("SELECT COALESCE(SUM(amount),0), COUNT(*) FROM funding_events").fetchone()
    resets = conn.execute("SELECT COALESCE(SUM(amount),0), COUNT(*) FROM funding_events WHERE kind = 'RESET'").fetchone()
    initial = conn.execute("SELECT COALESCE(SUM(amount),0) FROM funding_events WHERE kind = 'INITIAL'").fetchone()[0]
    staked = conn.execute("SELECT COALESCE(SUM(stake),0) FROM bets").fetchone()[0]
    returned = conn.execute("SELECT COALESCE(SUM(return_amount),0) FROM bets WHERE result_status IN ('WIN','LOSS','VOID')").fetchone()[0]
    open_stakes = conn.execute("SELECT COALESCE(SUM(stake),0) FROM bets WHERE result_status IN ('PENDING','UNRESOLVED')").fetchone()[0]
    pl = conn.execute("SELECT COALESCE(SUM(profit_loss),0) FROM bets WHERE result_status IN ('WIN','LOSS')").fetchone()[0]
    settled_stake = conn.execute("SELECT COALESCE(SUM(stake),0) FROM bets WHERE result_status IN ('WIN','LOSS')").fetchone()[0]
    cash = fund[0] - staked + returned
    return {"cash": round(cash, 2), "open_stakes": round(open_stakes, 2), "equity": round(cash + open_stakes, 2), "funding_total": round(fund[0], 2), "initial_funding": round(initial, 2),
            "reset_count": resets[1], "reset_funding_added": round(resets[0], 2), "betting_pl": round(pl, 2), "settled_stake": round(settled_stake, 2),
            "roi": (round(pl / settled_stake, 4) if settled_stake else None)}


def _fund(conn, now: dt.datetime, kind: str, amount: float, reason: str, day: int | None, note: str | None = None) -> dict:
    before = balance(conn)["cash"]
    conn.execute("INSERT INTO funding_events (at_utc, at_et, kind, balance_before, amount, balance_after, experiment_day, reason, note) VALUES (?,?,?,?,?,?,?,?,?)",
                 (_utc(now), _et(now), kind, before, round(amount, 2), round(before + amount, 2), day, reason, note))
    return {"kind": kind, "balance_before": before, "amount": round(amount, 2), "balance_after": round(before + amount, 2)}


def ensure_funded(conn, now: dt.datetime) -> None:
    if conn.execute("SELECT 1 FROM funding_events WHERE kind = 'INITIAL'").fetchone() is None:
        _fund(conn, now, "INITIAL", INITIAL_BANKROLL, "INITIAL_EXPERIMENT_FUNDING", phase(now)["day"] or 1, "owner correction 2026-10-20: the experiment bankroll starts at $500.00")
        conn.commit()


def reset_if_needed(conn, now: dt.datetime, day: int) -> dict | None:
    """Only while the experiment is active: if the bankroll cannot fund the next $10 bet, reset it to $500.00 with a logged, non-performance funding event."""
    if phase(now)["phase"] != "ACTIVE":
        return None
    cash = balance(conn)["cash"]
    if cash + 1e-9 >= STAKE:
        return None
    ev = _fund(conn, now, "RESET", RESET_TO - cash, RESET_REASON, day)
    return ev


# --------------------------------------------------------------------------------------------------- selection
def band_of(american: float) -> str:
    for lo, hi, label in PRICE_BANDS:
        if lo <= american <= hi:
            return label
    return "worse than -150"


def _decimal(american: float) -> float:
    return 1.0 + (american / 100.0 if american > 0 else 100.0 / abs(american))


def settlement_market_id(leg) -> str | None:
    from operational import paper_bet_settlement_driver as drv
    return drv._leg_settlement_market_id({"market_family": leg.market_family, "threshold": leg.threshold})


def _stat(family: str) -> str:
    return "SOG" if family in ("PLAYER_SOG", "PLAYER_SOG_ALTERNATE") else family


def exclusive_key(game_id, participant_id, family) -> tuple:
    """What makes two bets the same exposure or opposite sides of one event: a game's moneyline (either side), or a player's statistic in a game (any threshold, either shots family).
    At most one bet per key per day; the better-ranked one wins."""
    if family == "MONEYLINE":
        return (str(game_id), "MONEYLINE")
    return (str(game_id), str(participant_id), _stat(family))


def economic_key(leg) -> str:
    raw = "|".join(str(x) for x in (COHORT, leg.game_id, leg.participant_id, leg.market_family, leg.threshold, leg.side))
    return hashlib.sha256(raw.encode()).hexdigest()


def probability_basis(leg) -> tuple[float, str]:
    """(the probability recorded as conservative_probability, how it was formed). Never above the source leg's own number. Shots legs use the book's anchored record estimate (the player's own record held to
    the model + 0.02, and to 70% for 3+ shots) whenever it exists, so the recorded number is the one the normal book would have used; points, goals and saves have no capped record estimate and carry the calibrated
    model's own probability, labelled unanchored; a moneyline is the market's own no-vig chance."""
    from research.real_market_parlay import anchor
    base = float(leg.conservative_probability)
    fam = leg.market_family
    if fam in anchor.SHOT_FAMILIES:
        est = anchor.leg_estimate(leg)
        if est is not None:
            return min(base, float(est.p)), "ANCHORED_RECORD_ESTIMATE"
        if leg.model_probability is not None:
            return min(base, float(leg.model_probability), anchor.HIGH_LINE_CAP if (leg.threshold or 0) >= 3 else 1.0), "MODEL_PROBABILITY_RECORD_NOT_USABLE"
        return base, "SOURCE_ESTIMATE_ONLY"
    if fam == "MONEYLINE":
        mp = leg.market_probability
        if mp is not None and 0.0 < float(mp) < 1.0:
            return float(mp), "MARKET_NO_VIG"
        return base, "SOURCE_ESTIMATE_ONLY"
    return base, "MODEL_PROBABILITY_UNANCHORED"


def edge_class(edge: float, basis: str | None) -> tuple[str, str]:
    """(class, reason), assigned at recording time from the estimate's own edge. A positive edge only counts as POSITIVE_EDGE when the estimate is anchored (the player's record held to the model, or the market's own
    chance); a positive number on an unanchored model probability is NEUTRAL_UNCERTAIN_EDGE: it is not supported by a capped, record-based estimate."""
    if edge < -EDGE_BAND:
        return EDGE_NEGATIVE, f"estimated edge {edge:+.1%} is below -{EDGE_BAND:.0%}: recorded to fill the learning sample"
    if edge >= EDGE_BAND:
        if basis in ANCHORED_BASES:
            return EDGE_POSITIVE, f"estimated edge {edge:+.1%} on an anchored estimate"
        return EDGE_NEUTRAL, f"estimated edge {edge:+.1%} but the estimate is unanchored ({basis}): uncertain"
    return EDGE_NEUTRAL, f"estimated edge {edge:+.1%} is within {EDGE_BAND:.0%} of zero"


def prepare(leg):
    """The leg as the cohort will rank and record it: the estimate re-formed by probability_basis (never above the source's), the basis kept with it."""
    p, basis = probability_basis(leg)
    return dataclasses.replace(leg, conservative_probability=round(p, 4)), basis


def calibration_confidence(leg) -> float:
    """How much evidence stands behind THIS estimate: a shots leg's own recent record (games behind it, this season's games weigh more); the others rest on the model's held-out validation alone."""
    if leg.empirical_games:
        return round(min(1.0, (leg.empirical_games + (leg.empirical_games_current or 0)) / 45.0), 4)
    return 0.5 if leg.market_family != "MONEYLINE" else 0.3


def score_leg(leg, now: dt.datetime, basis: str | None = None) -> dict:
    from operational import best_bets
    dec = _decimal(leg.american_price)
    implied = 1.0 / dec
    cons = float(leg.conservative_probability)
    model = float(leg.model_probability if leg.model_probability is not None else cons)
    edge = cons - implied
    ev = cons * dec - 1.0
    start = best_bets.effective_start(leg.provider_start_utc or leg.game_start_utc, leg.game_start_utc) if (leg.provider_start_utc or leg.game_start_utc) else None
    limit = best_bets.price_age_limit_min((start - now).total_seconds() / 3600.0) if start else 100.0
    fresh = 0.5 if leg.quote_age_min is None else max(0.0, min(1.0, 1.0 - float(leg.quote_age_min) / float(limit or 100.0)))
    comp = {"conservative_probability": cons, "model_probability": model, "edge": max(0.0, min(1.0, (edge + 0.10) / 0.20)), "calibration_confidence": calibration_confidence(leg),
            "freshness": fresh, "model_quality": MODEL_QUALITY.get(leg.market_family, 0.0) * (1.0 if basis in ("ANCHORED_RECORD_ESTIMATE", None) or leg.market_family not in ("PLAYER_SOG", "PLAYER_SOG_ALTERNATE") else UNANCHORED_SHOTS_FACTOR)}
    score = sum(SELECTION_WEIGHTS[k] * comp[k] for k in SELECTION_WEIGHTS)
    return {"score": round(score, 5), "components": {k: round(v, 4) for k, v in comp.items()}, "implied": round(implied, 5), "edge_value": round(edge, 5), "ev": round(ev, 5), "decimal": round(dec, 5)}


def eligibility_reasons(leg, now: dt.datetime) -> list[str]:
    """Why a leg is NOT a legitimate cohort bet right now (empty = eligible). Every accuracy and integrity gate stays hard: event, identity, market, threshold, contract, a real price observation with a
    valid time, game not started, settlement path, estimate. The quote's AGE is not a gate here (owner policy 2026-10-21): it is recorded with the bet (price_observation). A quote with a missing,
    malformed or future time cannot be tied to a real observation and is rejected."""
    from operational import daily_tickets
    from research.real_market_parlay import engine as rmp
    why = []
    if leg.market_family not in ELIGIBLE_FAMILIES:
        why.append("market not supported for the cohort")
    if leg.american_price < ODDS_FLOOR:
        why.append(f"price worse than the {ODDS_FLOOR:+.0f} floor")
    for flag, label in (("provider_contract_verified", "market contract not verified"), ("model_threshold_eligible", "no eligible model estimate for this threshold"), ("identity_resolved", "identity not resolved"),
                        ("event_not_started", "event started")):
        if not getattr(leg, flag, False):
            why.append(label)
    status = str(getattr(leg, "freshness_status", "") or "").upper()
    if status not in OLD_BUT_REAL_STATUSES:
        why.append(f"quote time not valid ({leg.freshness_status})")
    elif not getattr(leg, "price_fresh", True) and status not in ("STALE_QUOTE", "STALE_RETRIEVAL"):
        why.append("price flagged for a reason other than its age")
    elif price_observation(leg.retrieved_at_utc, leg.quote_updated_utc or leg.captured_at_utc, now) is None:
        why.append("price cannot be tied to a real observation (missing, malformed or future time)")
    if not (0.0 < float(leg.conservative_probability) < 1.0):
        why.append("no usable probability estimate")
    if settlement_market_id(leg) is None:
        why.append("no settlement path")
    if not why and not rmp.leg_is_eligible(dataclasses.replace(leg, price_fresh=True)):          # the engine's own gate, literally (market and threshold allowlists, flags); only the age verdict is waived
        why.append("not eligible under the engine's leg gate (market, threshold or estimate)")
    if not why:
        why += daily_tickets.revalidate_before_recording(SimpleNamespace(legs=[leg]), now, price_age_is_advisory=True)        # identity on a current official roster and game start, at this very moment
    return why


def _reason_label(reason: str) -> str:
    """A reason with the leg's own label and the changing detail removed, so shortfall reasons aggregate ('price no longer fresh', not one line per leg)."""
    r = reason.split(": ", 1)[-1]
    return r.split(" (")[0].strip()


def _own_final_window(leg, now: dt.datetime) -> bool:
    """True when this game's puck drop is within RELAX_MIN (or has passed): no better price for it can still arrive."""
    from operational import best_bets
    raw = leg.provider_start_utc or leg.game_start_utc
    if not raw:
        return False
    start = best_bets.effective_start(raw, leg.game_start_utc)
    return (start - now).total_seconds() / 60.0 <= RELAX_MIN


def day_waves(nhl_conn, et_date: str) -> tuple[dict, int, dict]:
    """({game_id: wave index}, games_today, {wave index: first puck drop}) for the Eastern date: puck-drop waves (games within 90 minutes of a wave's first game), over ALL of the day's games whatever their state."""
    rows = nhl_conn.execute("SELECT game_id, scheduled_start_utc FROM games WHERE game_date = ? ORDER BY scheduled_start_utc", (et_date,)).fetchall()
    first, idx, game_wave, wave_start = None, -1, {}, {}
    for r in rows:
        if not r["scheduled_start_utc"]:
            continue
        t = dt.datetime.fromisoformat(r["scheduled_start_utc"].replace("Z", "+00:00"))
        t = t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)
        if first is None or (t - first).total_seconds() / 60.0 > WAVE_SPAN_MIN:
            idx, first = idx + 1, t
            wave_start[idx] = t
        game_wave[str(r["game_id"])] = idx
    return game_wave, len(game_wave), wave_start


def cumulative_caps(game_wave: dict, total: int, wave_start: dict | None = None, now: dt.datetime | None = None) -> dict[int, int]:
    """Slots a wave (with all earlier waves) may have used: the day's ten released in proportion to the games played through it. Just before a wave's puck drop the cap relaxes to
    ten less a reserve of one slot per later wave still to start (at most two), so a quiet late slate cannot strand slots that these prices could still fill."""
    per = Counter(game_wave.values())
    caps, run = {}, 0
    for w in sorted(per):
        run += per[w]
        cap = DAILY_TARGET if run >= total else min(DAILY_TARGET, math.ceil(DAILY_TARGET * run / total))
        if wave_start and now and w in wave_start and cap < DAILY_TARGET:
            if (wave_start[w] - now).total_seconds() / 60.0 <= RELAX_MIN:
                later = sum(1 for x in wave_start if x > w and wave_start[x] > now)
                cap = max(cap, DAILY_TARGET - min(RELAXED_RESERVE_MAX, later))
        caps[w] = cap
    return caps


def _penalty(cand: dict, chosen: list[dict]) -> float:
    p = 0.0
    for c in chosen:
        if c["leg"].game_id == cand["leg"].game_id:
            p += DIVERSIFICATION_PENALTY["same_game"]
        if c["leg"].participant_id == cand["leg"].participant_id:
            p += DIVERSIFICATION_PENALTY["same_player"]
        if c["leg"].team and c["leg"].team == cand["leg"].team:
            p += DIVERSIFICATION_PENALTY["same_team"]
        if c["band"] == cand["band"]:
            p += DIVERSIFICATION_PENALTY["same_price_band"]
    fam = sum(1 for c in chosen if c["leg"].market_family == cand["leg"].market_family)
    return p + DIVERSIFICATION_PENALTY["same_market_family_over_4"] * max(0, fam - 3)


def select(cands: list[dict], already: list[dict], slots: int) -> list[dict]:
    """Greedy best-first by score minus the diversification penalty against what is already chosen (the day's earlier bets included). Deterministic: ties break on the economic key."""
    chosen, pool = list(already), list(cands)
    picked = []
    while pool and len(picked) < slots:
        best = max(pool, key=lambda c: (0 if c.get("edge_class") == EDGE_NEGATIVE else 1, round(c["score"] - _penalty(c, chosen), 6), c["key"]))          # a negative-edge candidate only fills what the others leave
        best["penalty"] = round(_penalty(best, chosen), 4)
        chosen.append(best)
        picked.append(best)
        pool.remove(best)
    return picked


# --------------------------------------------------------------------------------------------------- recording
def _eggy_legs() -> dict:
    """Today's Eggy official ticket legs (relation labelling only): {(game, participant, family): ticket_id}. The ticket card carries no threshold, so the relation is by player and market."""
    try:
        from operational import daily_tickets
        t = ((daily_tickets.read_state() or {}).get("primary") or {}).get("ticket") or {}
        return {(str(l.get("game_id")), str(l.get("participant_id")), l.get("market_family")): t.get("ticket_id") for l in t.get("legs") or []}
    except Exception:  # noqa: BLE001 - labelling must never stop recording
        return {}


def _record(conn, cand: dict, now: dt.datetime, day: int, rank: int, wave: int | None, dep: dict, eggy: dict) -> str:
    leg, sc = cand["leg"], cand["scored"]
    bet_id = "L" + cand["key"][:15].upper()
    rel = eggy.get((str(leg.game_id), str(leg.participant_id), leg.market_family))
    po = price_observation(leg.retrieved_at_utc, leg.quote_updated_utc or leg.captured_at_utc, now)
    if po is None:                                          # eligibility already required a real observation; this is only a guard against a clock edge case
        raise sqlite3.IntegrityError("no real price observation to record")
    reason = (f"{cand['edge_class']}; price {po['status']} ({po['age_min']:.0f} min since last observed); rank {rank} of the day: score {sc['score']:.3f} (conservative p {leg.conservative_probability:.0%}, edge {sc['edge_value']:+.1%} at {leg.american_price:+.0f}, "
              f"{leg.market_family} {leg.participant_name}{'' if leg.threshold is None else ' ' + str(leg.threshold) + '+'}); diversification penalty {cand.get('penalty', 0.0):.3f}")
    conn.execute(
        """INSERT INTO bets (bet_id, idempotency_key, experiment_id, cohort, policy_digest, et_date, experiment_day, selection_rank, wave_index, created_at_utc, created_at_et, game_id, event_id, event_start_utc,
           market_family, settlement_market_id, threshold, side, participant_id, participant_name, team, opponent, american_price, decimal_price, stake, model_probability, conservative_probability,
           implied_probability, edge, ev, selection_score, score_components_json, selection_reason, freshness_status, quote_updated_utc, retrieved_at_utc, quote_age_min, model_version, price_source,
           dependence_json, eggy_relation, leg_json, probability_basis, source_conservative_probability, edge_class, edge_class_reason,
           price_capture_timestamp, price_provider_update_utc, price_age_at_recording_min, provider_quote_age_at_recording_min, price_status, price_age_bucket) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (bet_id, cand["key"], EXPERIMENT_ID, COHORT, policy_digest(), et.eastern_today(now), day, rank, wave, _utc(now), _et(now), str(leg.game_id), leg.event_id, leg.game_start_utc,
         leg.market_family, settlement_market_id(leg), leg.threshold, leg.side, str(leg.participant_id), leg.participant_name, leg.team, leg.opponent, leg.american_price, sc["decimal"], STAKE,
         leg.model_probability, float(leg.conservative_probability), sc["implied"], sc["edge_value"], sc["ev"], sc["score"], json.dumps(sc["components"]), reason, leg.freshness_status,
         leg.quote_updated_utc, leg.retrieved_at_utc, leg.quote_age_min, leg.model_version, "LIVE_DRAFTKINGS", json.dumps(dep, sort_keys=True),
         f"SAME_PLAYER_AND_MARKET_AS_EGGY_PICK_LEG:{rel} (a single here, a parlay leg there; separate ledgers, nothing staked twice)" if rel else "EGGY_NOT_INVOLVED", json.dumps(dataclasses.asdict(leg), default=str), cand.get("basis"), cand.get("source_p"), cand["edge_class"], cand["edge_reason"],
         po["captured_utc"], po["provider_update_utc"], po["age_min"], po["provider_age_min"], po["status"], po["bucket"]))
    return bet_id


def _dependence(cand: dict, chosen: list[dict]) -> dict:
    leg = cand["leg"]
    return {"same_game_bets_before": sum(1 for c in chosen if c["leg"].game_id == leg.game_id), "same_player_bets_before": sum(1 for c in chosen if c["leg"].participant_id == leg.participant_id),
            "same_team_bets_before": sum(1 for c in chosen if leg.team and c["leg"].team == leg.team), "bets_before": len(chosen),
            "note": "bets in one game or on one player are positively dependent; analyse such bets together"}


def _today_rows(conn, et_date: str) -> list[dict]:
    return [dict(r) for r in conn.execute("SELECT * FROM bets WHERE et_date = ? ORDER BY selection_rank", (et_date,)).fetchall()]


def _paused() -> bool:
    """The owner's recording pause (operational/recording_pause.py), read only: a deliberate owner stop applies to every automatic paper bet. A losing bet, day or streak never sets it."""
    from operational import recording_pause
    return bool(recording_pause.is_paused())


def run_cycle(nhl_conn, now: dt.datetime, collected: dict | None, *, conn=None, collector=None) -> dict:
    """One trader cycle: settle whatever is open (always, whether or not the policy is currently approved or the window is open), then, only while the experiment is approved, inside its window and not
    paused by the owner, record up to the day's remaining slots from the best eligible bets, then publish the status document. Never raises into the trader."""
    own = conn is None
    summary: dict = {"status": "OK"}
    try:
        ph = phase(now)
        summary["phase"] = ph
        auth = is_authorised()
        if conn is None and not auth and not state_paths.path(DB_NAME).exists():
            return {"status": "NOT_AUTHORISED", "phase": ph, "reason": "the experiment policy has not been approved: nothing is recorded"}
        conn = conn or connect()
        if auth:
            ensure_funded(conn, now)
        summary["settlement"] = settle_due(conn, nhl_conn, now)
        if not auth:
            summary.update(status="NOT_AUTHORISED", recording={"status": "NOT_RECORDING", "reason": "the policy has not been approved, or its digest changed since approval; open bets still settle"})
        elif ph["phase"] != "ACTIVE":
            summary["recording"] = {"status": "NO_NEW_BETS", "reason": ("the experiment has ended; the normal paper-book policy continues" if ph["phase"] == "ENDED" else "the experiment has not started")}
        elif _paused():
            summary["recording"] = {"status": "PAUSED_BY_OWNER", "reason": "recording is paused by the owner; open bets still settle"}
        else:
            pool = collected
            if collector is not None:                  # the learning cohort's own pool: real quotes kept with their age (the normal pool excludes old quotes)
                try:
                    pool = collector()
                except Exception as exc:  # noqa: BLE001
                    pool = None
                    summary["recording"] = {"status": "POOL_UNAVAILABLE", "reason": f"{exc.__class__.__name__}: {exc}"}
            if pool is not None:
                summary.update(_record_cycle(conn, nhl_conn, now, pool or {}, ph))
        try:
            write_status(status_doc(now, conn=conn))
            if auth:
                summary["postmortem"] = _refresh_postmortem(conn, nhl_conn, now, summary)
        except Exception as exc:  # noqa: BLE001 - the status and postmortem files are informational
            summary["status_error"] = f"{exc.__class__.__name__}: {exc}"
        return summary
    except Exception as exc:  # noqa: BLE001 - the cohort must never stop the trader's own cycle
        return {"status": "ERROR", "error": f"{exc.__class__.__name__}: {exc}"}
    finally:
        if own and conn is not None:
            conn.close()


POSTMORTEM_REFRESH_MIN = 55


def _refresh_postmortem(conn, nhl_conn, now: dt.datetime, summary: dict) -> str:
    """Rebuild the daily postmortem document when something just settled or was recorded, when the Eastern date rolled over, or at least hourly (it is the morning-after read of the prior day)."""
    from operational import learning_postmortem as lpm
    cur = lpm.load()
    due = cur is None
    if cur is not None:
        try:
            age = (now - dt.datetime.fromisoformat(cur["generated_at_utc"].replace("Z", "+00:00"))).total_seconds() / 60.0
        except (KeyError, ValueError):
            age = 10 ** 6
        prior = (dt.date.fromisoformat(phase(now)["today"]) - dt.timedelta(days=1)).isoformat()
        due = age >= POSTMORTEM_REFRESH_MIN or cur.get("postmortem_for") != prior or bool((summary.get("settlement") or {}).get("settled")) \
            or bool((summary.get("recording") or {}).get("recorded_this_cycle"))
    if not due:
        return "CURRENT"
    return lpm.refresh(conn, nhl_conn, now)["status"]


def write_status(doc: dict) -> None:
    p = state_paths.path(STATUS_NAME)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(doc, indent=1, sort_keys=True, default=str))
    tmp.replace(p)


def load_status() -> dict | None:
    try:
        return json.loads(state_paths.path(STATUS_NAME).read_text())
    except (OSError, ValueError):
        return None


def _record_cycle(conn, nhl_conn, now: dt.datetime, collected: dict, ph: dict) -> dict:
    today, day = ph["today"], ph["day"]
    legs = collected.get("legs") or []
    game_wave, total_games, wave_start = day_waves(nhl_conn, today)
    caps = cumulative_caps(game_wave, total_games, wave_start, now) if total_games else {}
    strict_caps = cumulative_caps(game_wave, total_games) if total_games else {}
    rows = _today_rows(conn, today)
    have_keys = {r["idempotency_key"] for r in conn.execute("SELECT idempotency_key FROM bets")}          # all days: a rescheduled game keeps its game_id
    held_exposure = {exclusive_key(r["game_id"], r["participant_id"], r["market_family"]) for r in rows}
    reasons: Counter = Counter()
    # ONE observation per bet: the NEWEST real observation of that exact line, judged on its own. An older observation of the same bet (a better price from the morning, say) never stands in for a newer one
    # that fails a gate (a price that moved past the floor, a withdrawn market): the last observed price is the price.
    newest: dict[str, object] = {}
    for leg in legs:
        k = economic_key(leg)
        cur = newest.get(k)
        if cur is None or _observed_at(leg) > _observed_at(cur):
            newest[k] = leg
    by_key: dict[str, dict] = {}
    for key, leg in newest.items():
        if str(leg.game_id) not in game_wave:
            reasons["game is not on today's Eastern schedule"] += 1
            continue
        if key in have_keys:
            continue
        why = eligibility_reasons(leg, now)
        if why:
            reasons[_reason_label(why[0])] += 1
            continue
        pleg, basis = prepare(leg)
        if not (0.0 < float(pleg.conservative_probability) < 1.0):
            reasons["no usable probability estimate"] += 1
            continue
        sc = score_leg(pleg, now, basis)
        ecls, ereason = edge_class(sc["edge_value"], basis)
        by_key[key] = {"leg": pleg, "key": key, "scored": sc, "score": sc["score"], "band": band_of(leg.american_price), "wave": game_wave.get(str(leg.game_id)), "basis": basis,
                       "source_p": float(leg.conservative_probability), "edge_class": ecls, "edge_reason": ereason}
    cands, claimed = [], set(held_exposure)
    for c in sorted(by_key.values(), key=lambda c: (c["edge_class"] == EDGE_NEGATIVE, -c["score"], c["key"])):          # a negative-edge candidate never shadows a better-classed alternative of the same exposure
        ek = exclusive_key(c["leg"].game_id, c["leg"].participant_id, c["leg"].market_family)
        if ek in claimed:
            reasons["same exposure as a better-ranked or held bet (both sides of a moneyline, or another threshold of one player's statistic)"] += 1
            continue
        claimed.add(ek)
        cands.append(c)
    chosen_before = [{"leg": SimpleNamespace(game_id=r["game_id"], participant_id=r["participant_id"], team=r["team"], market_family=r["market_family"]), "band": band_of(r["american_price"])} for r in rows]
    eggy = _eggy_legs()
    recorded_now, rank, held_negative = [], len(rows), 0
    per_wave_before = Counter(r["wave_index"] for r in rows)
    waves_with_cands = sorted({c["wave"] for c in cands if c["wave"] is not None})
    for w in waves_with_cands:
        cap = caps.get(w, DAILY_TARGET)
        used = sum(n for ww, n in per_wave_before.items() if ww is not None and ww <= w) + sum(1 for c in recorded_now if c["wave"] <= w)
        room = max(0, min(cap, DAILY_TARGET) - used)
        room = min(room, DAILY_TARGET - (len(rows) + len(recorded_now)))
        if room <= 0:
            continue
        others = [c for c in cands if c["wave"] == w and c not in recorded_now]
        picks = select([c for c in others if c["edge_class"] != EDGE_NEGATIVE], chosen_before + recorded_now, room)
        neg = [c for c in others if c["edge_class"] == EDGE_NEGATIVE]
        ready = [c for c in neg if _own_final_window(c["leg"], now)]          # no better price for THIS game can still arrive
        held_negative += len(neg) - len(ready)
        strict_room = max(0, min(strict_caps.get(w, DAILY_TARGET), DAILY_TARGET) - (used + len(picks)))          # a negative-edge fill never uses the relaxed headroom held for better candidates
        neg_room = min(room - len(picks), strict_room)
        if ready and neg_room > 0:
            picks += select(ready, chosen_before + recorded_now + picks, neg_room)
        for c in picks:
            dep = _dependence(c, chosen_before + recorded_now)
            try:
                conn.execute("SAVEPOINT bet")
                reset = reset_if_needed(conn, now, day)
                _record(conn, c, now, day, rank + 1, w, dep, eggy)
                conn.execute("RELEASE bet")
            except sqlite3.IntegrityError:                     # a duplicate the ledger refused: skip this one bet, never abort the cycle
                conn.execute("ROLLBACK TO bet")
                conn.execute("RELEASE bet")
                reasons["duplicate refused by the ledger"] += 1
                continue
            rank += 1
            c["rank"], c["reset"] = rank, reset
            recorded_now.append(c)
    recorded_total = len(rows) + len(recorded_now)
    open_slots = DAILY_TARGET - recorded_total
    shortfall = None
    if open_slots > 0:
        waiting_caps = {w: caps.get(w) for w in sorted(set(game_wave.values()))}
        shortfall = {"target": DAILY_TARGET, "recorded": recorded_total, "slots_open": open_slots, "eligible_not_yet_recorded": len([c for c in cands if c not in recorded_now]), "negative_edge_candidates_held_until_the_final_window": held_negative,
                     "legs_in_pool": len(legs), "not_eligible_reasons": dict(reasons.most_common(12)), "wave_caps": waiting_caps,
                     "sources": {k: (v.get("exclusion_reasons") if isinstance(v, dict) else None) for k, v in (collected.get("sources") or {}).items() if isinstance(v, dict) and v.get("exclusion_reasons")},
                     "why": ("fewer than 10 legitimate eligible bets exist so far today; slots are released with the puck-drop waves (later waves' prices are not captured yet)"
                             if open_slots else None)}
    conn.execute("INSERT INTO daily_records (et_date, experiment_day, target, recorded, eligible_now, slots_open, shortfall_json, updated_at_utc) VALUES (?,?,?,?,?,?,?,?) "
                 "ON CONFLICT(et_date) DO UPDATE SET recorded=excluded.recorded, eligible_now=excluded.eligible_now, slots_open=excluded.slots_open, shortfall_json=excluded.shortfall_json, updated_at_utc=excluded.updated_at_utc",
                 (today, day, DAILY_TARGET, recorded_total, len(cands), max(0, open_slots), json.dumps(shortfall, sort_keys=True) if shortfall else None, _utc(now)))
    conn.commit()
    return {"recording": {"day": day, "recorded_this_cycle": len(recorded_now), "recorded_today": recorded_total, "target": DAILY_TARGET, "eligible_pool": len(cands),
                          "resets": [c["reset"] for c in recorded_now if c.get("reset")], "shortfall": shortfall}}


# --------------------------------------------------------------------------------------------------- settlement
def settle_due(conn, nhl_conn, now: dt.datetime) -> dict:
    """Settle every open bet whose game is final, through the same resolvers and payout formulas as the normal paper book (idempotent: a settled bet is never touched again)."""
    from operational import paper_bankroll as pb
    from operational import paper_bet_settlement_driver as drv
    out = {"scanned": 0, "settled": 0, "still_pending": 0}
    rows = conn.execute("SELECT * FROM bets WHERE result_status IN ('PENDING','UNRESOLVED')").fetchall()          # a game that is not final answers "still waiting"; no string-compared clock here
    for r in rows:
        out["scanned"] += 1
        # The same resolver the main book uses for a ticket, applied to this one-leg ticket: identical did-not-play (UNRESOLVED while the void rule is unverified), final-game and stat-revision handling.
        result = drv.resolve_combo_bet(nhl_conn, {"legs_json": json.dumps([json.loads(r["leg_json"])])})
        outcome = drv.terminal_outcome(result)
        if outcome is None:
            out["still_pending"] += 1
            continue
        status = outcome["status"]
        if status == "UNRESOLVED" and r["result_status"] == "UNRESOLVED":
            continue
        odds = outcome.get("settled_odds") or r["american_price"]
        pl = pb.compute_payout(r["stake"], odds, status)
        ret = {"WIN": r["stake"] + pl, "LOSS": 0.0, "VOID": r["stake"]}.get(status)
        conn.execute("UPDATE bets SET result_status=?, settled_at_utc=?, settled_odds=?, profit_loss=?, return_amount=?, notes=?, settlement_json=? WHERE bet_id = ? AND result_status IN ('PENDING','UNRESOLVED')",
                     (status, _utc(now), odds, pl if status in ("WIN", "LOSS") else 0.0, ret, outcome.get("notes"), outcome.get("detail_json"), r["bet_id"]))
        out["settled"] += 1
    conn.commit()
    return out


# --------------------------------------------------------------------------------------------------- activation
def activate(now: dt.datetime, *, approved_by: str, evidence: str, conn=None) -> dict:
    """Owner approval of this exact policy digest and the initial funding. Called by deploy/activate_learning_experiment.py only."""
    own = conn is None
    conn = conn or connect()
    try:
        ensure_funded(conn, now)
    finally:
        if own:
            conn.close()
    doc = {"approved": True, "approved_at_utc": _utc(now), "approved_by": approved_by, "policy_digest": policy_digest(), "policy": POLICY, "evidence": evidence}
    p = state_paths.path(STATE_NAME)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(doc, indent=1, sort_keys=True))
    tmp.replace(p)
    return doc


def status_doc(now: dt.datetime | None = None, conn=None) -> dict:
    """The compact status published with the snapshot and shown on Paper Performance."""
    now = now or dt.datetime.now(dt.timezone.utc)
    ph = phase(now)
    own = conn is None
    conn = conn or connect()
    try:
        today = ph["today"]
        rows = _today_rows(conn, today)
        bal = balance(conn)
        total = conn.execute("SELECT COUNT(*) FROM bets").fetchone()[0]
        settled = conn.execute("SELECT SUM(result_status='WIN'), SUM(result_status='LOSS'), SUM(result_status='VOID'), SUM(result_status IN ('PENDING','UNRESOLVED')) FROM bets").fetchone()
        daily = conn.execute("SELECT * FROM daily_records WHERE et_date = ?", (today,)).fetchone()
        resets = [dict(r) for r in conn.execute("SELECT at_et, kind, balance_before, amount, balance_after, experiment_day, reason FROM funding_events WHERE kind = 'RESET' ORDER BY id").fetchall()]
    finally:
        if own:
            conn.close()
    wins, losses = int(settled[0] or 0), int(settled[1] or 0)
    return {"generated_at_utc": _utc(now), "cohort": COHORT, "label": "14-DAY POSTMORTEM LEARNING EXPERIMENT", "authorised": is_authorised(), "policy_digest": policy_digest(), "phase": ph["phase"], "day": ph["day"], "days": 14,
            "start_date": START_DATE, "end_date": END_DATE, "target_per_day": DAILY_TARGET, "stake_usd": STAKE, "odds_requirement": f"{int(ODDS_FLOOR)} or better",
            "recorded_today": len(rows), "exposure_today_usd": round(len(rows) * STAKE, 2), "bankroll": bal, "resets": resets, "bets_total": total,
            "record": {"wins": wins, "losses": losses, "voids": int(settled[2] or 0), "open": int(settled[3] or 0), "hit_rate": round(wins / (wins + losses), 4) if wins + losses else None},
            "edge_classes_today": dict(Counter(r["edge_class"] for r in rows)), "bets_today": [{k: r[k] for k in ("selection_rank", "market_family", "participant_name", "team", "threshold", "american_price", "conservative_probability", "edge", "edge_class", "price_status", "price_age_at_recording_min", "result_status", "profit_loss", "eggy_relation")} for r in rows],
            "shortfall_today": (json.loads(daily["shortfall_json"]) if daily and daily["shortfall_json"] else None),
            "eggy": {"official_picks_per_day": 1, "note": "Eggy's Parlay Pick of the Day is separate: one official pick a day, its own ledger"},
            "ends_automatically": "2026-11-03 (Eastern): no new bets, the reset rule stops, open bets still settle"}
