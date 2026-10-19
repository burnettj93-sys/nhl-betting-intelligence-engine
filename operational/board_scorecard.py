"""Board scorecard: the pregame-frozen Slate Board cards graded against official results.

Why this exists (found 2026-10-19): the prospective ledger and its scoring (operational/prediction_evaluation.py, model_scorecard.py) are fed by the retired T-35 and sweep jobs and by a shots corpus that
ends in April 2026, so nothing the v4 board shows (anytime-goal scorers, shots ladder, goalie saves, moneyline) was being scored. The board already freezes every game's card at puck drop
(operational/slate_board.py -> slate_board_pregame.json) with the model probabilities, the DraftKings price and the quote time. This module grades those frozen cards once the game is FINAL.

Read-only against the engine: it never writes the prospective ledger or the paper bankroll, never touches a model or threshold, and spends no credit. Its only output is board_scorecard.json (one graded
row per frozen selection, written once and never revised) plus a summary. Players who did not play are excluded (the book voids them), and a goalie other than the card's starter is excluded from the saves grade.

Reading the summary: Brier is lower-is-better. A market-implied Brier is shown beside the model's where the card carries a price. No verdict is given below MIN_FOR_A_READING graded selections, and the
sample is selections from displayed picks, not every player.
"""
from __future__ import annotations

import datetime as dt
import json

from operational import state_paths

PREGAME_STATE = "slate_board_pregame.json"
OUT = "board_scorecard.json"
MIN_FOR_A_READING = 30


def _load(name: str) -> dict:
    try:
        return json.loads(state_paths.path(name).read_text())
    except (OSError, ValueError):
        return {}


def _implied(american) -> float | None:
    try:
        a = float(american)
    except (TypeError, ValueError):
        return None
    return 100.0 / (a + 100.0) if a > 0 else (-a) / (-a + 100.0) if a < 0 else None


def _player_row(conn, gid, pid):
    return conn.execute("select goals, shots, played, toi_minutes from player_game_stats where game_id=? and player_id=? order by revision_number desc limit 1", (int(gid), int(pid))).fetchone()


def _dnp(st) -> bool:
    """No row, not marked played, or no ice time: the book voids the selection, so it is excluded rather than graded a loss."""
    return st is None or not st[2] or not st[3]


def _goalie_row(conn, gid, pid):
    return conn.execute("select started, shots_against, saves from goalie_game_stats where game_id=? and player_id=? order by revision_number desc limit 1", (int(gid), int(pid))).fetchone()


def grade_card(card: dict, conn) -> list[dict]:
    """Graded rows for one frozen card whose game is FINAL. Each row: key, market, p (model probability), y (1/0), market_p (price-implied, with the vig) where the card has one, plus the identity fields."""
    gid = str(card["game_id"])
    g = conn.execute("select game_state, home_team, away_team, home_score, away_score from games where game_id=?", (int(gid),)).fetchone()
    if not g or g[0] != "FINAL" or g[3] is None or g[4] is None:
        return []
    if conn.execute("select 1 from player_game_stats where game_id=? limit 1", (int(gid),)).fetchone() is None:
        return []                                                  # FINAL but the box score is not stored yet: grade on a later cycle, never as "did not play"
    base = {"game_id": gid, "matchup": f"{card['away']}@{card['home']}", "frozen_at_utc": card.get("_stored_at_utc")}
    rows: list[dict] = []
    for s in card.get("scorers") or []:
        st = _player_row(conn, gid, s["player_id"])
        if _dnp(st):
            rows.append({**base, "key": f"{gid}:goal:{s['player_id']}", "market": "ANYTIME_GOAL", "player": s["name"], "excluded": "DID_NOT_PLAY"})
            continue
        if s.get("probability") is None:
            continue
        rows.append({**base, "key": f"{gid}:goal:{s['player_id']}", "market": "ANYTIME_GOAL", "player": s["name"], "p": s["probability"], "y": int(st[0] >= 1),
                     "market_p": (s.get("price") or {}).get("implied"), "market_basis": "PRICE_INCLUDING_VIG", "quote_updated_utc": (s.get("price") or {}).get("quote_updated_utc")})
    for p in (card.get("shots") or {}).get("picks") or []:
        ch = p.get("chosen") or {}
        if not ch.get("threshold"):
            continue
        st = _player_row(conn, gid, p["player_id"])
        key = f"{gid}:shots:{p['player_id']}:{ch['threshold']}"
        if _dnp(st):
            rows.append({**base, "key": key, "market": f"SHOTS_{ch['threshold']}PLUS", "player": p["name"], "excluded": "DID_NOT_PLAY"})
            continue
        if ch.get("model") is None:
            continue
        rows.append({**base, "key": key, "market": f"SHOTS_{ch['threshold']}PLUS", "player": p["name"], "p": ch.get("model"), "y": int(st[1] >= ch["threshold"]),
                     "market_p": _implied(ch.get("american")), "market_basis": "PRICE_INCLUDING_VIG", "quote_updated_utc": p.get("quote_updated_utc")})
    for gl in card.get("goalies") or []:
        line, starter = gl.get("line") or {}, gl.get("starter") or {}
        if not starter.get("player_id") or line.get("line") is None or not (gl.get("projection") or {}).get("expected_saves"):
            continue
        key = f"{gid}:saves:{starter['player_id']}"
        gr = _goalie_row(conn, gid, starter["player_id"])
        if gr is None and conn.execute("select 1 from goalie_game_stats where game_id=? limit 1", (int(gid),)).fetchone() is None:
            continue                                               # goalie box score not stored yet: later cycle
        if gr is None or not gr[0]:
            rows.append({**base, "key": key, "market": "GOALIE_SAVES_LINE", "player": starter.get("name"), "excluded": "CARD_STARTER_DID_NOT_START"})
            continue
        exp = gl["projection"]["expected_saves"]
        rows.append({**base, "key": key, "market": "GOALIE_SAVES_LINE", "player": starter.get("name"), "expected_saves": exp, "line": line["line"], "actual_saves": gr[2],
                     "model_side": "OVER" if exp > line["line"] else "UNDER" if exp < line["line"] else "EVEN", "went": "OVER" if gr[2] > line["line"] else "UNDER" if gr[2] < line["line"] else "PUSH",
                     "quote_updated_utc": line.get("quote_updated_utc")})
    ml = ((card.get("side") or {}).get("sides")) or {}
    if ml.get("home") and ml.get("away") and ml["home"].get("model") is not None:
        home_won = int(g[3] > g[4])
        rows.append({**base, "key": f"{gid}:moneyline", "market": "MONEYLINE_HOME", "player": None, "p": ml["home"]["model"], "y": home_won, "market_p": ml["home"].get("no_vig"), "market_basis": "NO_VIG",
                     "quote_updated_utc": ml["home"].get("quote_captured_at_utc")})
    return rows


def score_final_games(conn, *, write: bool = True) -> dict:
    """Grade every frozen card whose game is now FINAL and not yet graded. Idempotent: a graded selection is never revised."""
    doc = _load(OUT)
    graded = doc.get("rows") or {}
    new = 0
    skipped: list[dict] = []
    for day, cards in _load(PREGAME_STATE).items():
        if day.startswith("_") or not isinstance(cards, dict):
            continue
        for gid, card in cards.items():
            if not isinstance(card, dict) or "scorers" not in card:
                continue
            try:
                card_rows = grade_card(card, conn)
            except Exception as exc:  # noqa: BLE001 - one malformed card must not stop the others or lose what was graded this pass
                skipped.append({"game_id": str(gid), "error": f"{exc.__class__.__name__}: {str(exc)[:120]}"})
                continue
            for r in card_rows:
                if r["key"] not in graded:
                    graded[r["key"]] = {**r, "date_et": day}
                    new += 1
    doc = {"rows": graded, "generated_at_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "min_for_a_reading": MIN_FOR_A_READING}
    if write and new:
        p = state_paths.path(OUT)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(doc, indent=1, sort_keys=True))
        tmp.replace(p)
    return {"newly_graded": new, "total": len(graded), **({"cards_skipped": skipped} if skipped else {})}


def safe_score(conn) -> dict:
    """The trader's one call: grade what is final, never raise, never block the cycle."""
    try:
        return score_final_games(conn)
    except Exception as exc:  # noqa: BLE001
        return {"status": "ERROR", "error": f"{exc.__class__.__name__}: {exc}"}


def summarize(rows: dict | None = None) -> dict:
    """Per market: how many selections were graded (and over how many games), the model's hit rate and Brier, and, ONLY on the rows that carried a price, the model's and the price's Brier side by side. The price
    is the book's own (vig included) for scorers and shots and the no-vig moneyline for the moneyline, so a price figure is a yardstick, not a fair-odds claim; it is labelled that way."""
    rows = rows if rows is not None else (_load(OUT).get("rows") or {})
    out: dict = {}
    for r in rows.values():
        m = out.setdefault(r["market"], {"graded": 0, "games": set(), "excluded": 0, "hits": 0, "brier_sum": 0.0, "p_sum": 0.0, "priced_n": 0, "priced_model_sum": 0.0, "priced_market_sum": 0.0, "basis": set(),
                                         "saves_n": 0, "saves_abs_err": 0.0, "saves_side_right": 0, "saves_pushes": 0})
        if r.get("excluded"):
            m["excluded"] += 1
            continue
        m["graded"] += 1
        m["games"].add(r.get("game_id"))
        if r["market"] == "GOALIE_SAVES_LINE":
            m["saves_n"] += 1
            m["saves_abs_err"] += abs(r["actual_saves"] - r["expected_saves"])
            if r["went"] == "PUSH" or r["model_side"] == "EVEN":
                m["saves_pushes"] += 1
            elif r["went"] == r["model_side"]:
                m["saves_side_right"] += 1
            continue
        m["hits"] += r["y"]
        m["p_sum"] += r["p"]
        m["brier_sum"] += (r["p"] - r["y"]) ** 2
        if r.get("market_p") is not None:
            m["priced_n"] += 1
            m["priced_model_sum"] += (r["p"] - r["y"]) ** 2
            m["priced_market_sum"] += (r["market_p"] - r["y"]) ** 2
            m["basis"].add(r.get("market_basis") or "UNLABELLED")
    final = {}
    for k, m in out.items():
        n = m["graded"]
        row = {"graded": n, "games": len(m["games"]), "excluded_did_not_play_or_start": m["excluded"],
               "reading": ("ENOUGH_FOR_A_FIRST_READING" if n >= MIN_FOR_A_READING else f"TOO_FEW (need {MIN_FOR_A_READING})") + "; selections are the displayed picks and are correlated within a game"}
        if k == "GOALIE_SAVES_LINE":
            decided = m["saves_n"] - m["saves_pushes"]
            row.update({"mean_abs_error_saves": round(m["saves_abs_err"] / n, 2) if n else None, "model_side_right": f"{m['saves_side_right']} of {decided}" if decided else None})
        elif n:
            row.update({"hit_rate": round(m["hits"] / n, 3), "mean_model_probability": round(m["p_sum"] / n, 3), "model_brier": round(m["brier_sum"] / n, 4)})
            if m["priced_n"]:
                row.update({"priced_rows": m["priced_n"], "model_brier_on_priced_rows": round(m["priced_model_sum"] / m["priced_n"], 4),
                            "price_brier_on_priced_rows": round(m["priced_market_sum"] / m["priced_n"], 4), "price_basis": "+".join(sorted(m["basis"]))})
        final[k] = row
    return final


def main() -> int:
    import db
    conn = db.get_conn()
    res = safe_score(conn)
    print(json.dumps({"scored": res, "summary": summarize()}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
