"""
Recommendation audit: a representative sample of players checked the way the Hyry and Bourque audits were, end to end, against the stored records.

For every sampled player it checks
  * identity and team   -- the roster sync's team vs the team the product shows, and that the revalidation check raises no TEAM_CHANGED for his own next game
                           (the false-alert defect found in the Bourque audit);
  * small sample        -- fewer than MIN_GAMES_FOR_PRICING games must be flagged limited, carry no pricing eligibility and appear in no published option
                           (the over-prediction defect found in the Hyry audit);
  * explanation         -- the per-leg "why" lines exist, say "inferred" for the usage tier, and show "Reported by" only when a reported line/PP exists;
  * model sanity        -- the model's chance for shots 2+ and points 1+ tonight against his own rate over his last 60 games (a gross-disagreement flag, not a test).

Read-only; makes no purchase and no network call. Run: python3 deploy/audit_recommendations.py --out docs/validation/recommendation_audit.json
"""
from __future__ import annotations

import argparse
import datetime as dt
import gzip
import json
import random
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import db  # noqa: E402
from features import point_in_time as pit  # noqa: E402
from operational import bet_revalidation as brv, leg_context  # noqa: E402
from operational.pricing_policy import MIN_GAMES_FOR_PRICING  # noqa: E402

RUNTIME = REPO / "operational" / "runtime"
NAMED = ("Arttu Hyry", "Mavrik Bourque", "Blake Coleman")
DISAGREE = 0.20          # model vs own 60-game rate: flag a gap bigger than this for a look


def _history() -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    with gzip.open(RUNTIME / "product_skater_games.jsonl.gz", "rt") as f:
        for i, line in enumerate(f):
            if i == 0:
                continue                      # signature line
            r = json.loads(line)
            out.setdefault(str(r["player_id"]), []).append(r)
    for rows in out.values():
        rows.sort(key=lambda r: r["date"])
    return out


def sample(players: dict, seed: int, per_stratum: int) -> list[tuple[str, str]]:
    rng = random.Random(seed)
    items = [(pid, p) for pid, p in players.items() if p.get("position") != "G" and p.get("next_game")]
    chosen: list[tuple[str, str]] = []

    def take(label, pool):
        pool = sorted(pool, key=lambda kv: kv[0])
        for pid, _ in rng.sample(pool, min(per_stratum, len(pool))):
            if pid not in {c[0] for c in chosen}:
                chosen.append((pid, label))

    for name in NAMED:
        for pid, p in items:
            if p["name"] == name:
                chosen.append((pid, "named audit"))
    take("low sample (< pricing floor)", [kv for kv in items if (kv[1].get("games_total") or 0) < MIN_GAMES_FOR_PRICING])
    take("established forward, tier 1-2", [kv for kv in items if kv[1].get("position") != "D" and (kv[1].get("usage_tier") or 9) <= 2 and (kv[1].get("games_total") or 0) >= 200])
    take("established forward, tier 3-4", [kv for kv in items if kv[1].get("position") != "D" and (kv[1].get("usage_tier") or 0) >= 3 and (kv[1].get("games_total") or 0) >= 100])
    take("defenseman", [kv for kv in items if kv[1].get("position") == "D" and (kv[1].get("games_total") or 0) >= 100])
    return chosen


def audit_player(pid: str, stratum: str, p: dict, nhl_conn, hist: dict, now_iso: str, published_ids: set) -> dict:
    pr = p.get("projection") or {}
    nxt = p["next_game"]
    games = int(pr.get("games_observed", p.get("games_total", 0)) or 0)
    findings: list[str] = []
    checks: dict[str, dict] = {}

    # identity / team
    roster_team = pit.team_of_player(nhl_conn, pid, now_iso)
    leg = {"market_family": "PLAYER_POINTS", "participant_id": pid, "participant_name": p["name"], "game_id": nxt["game_id"]}
    reasons = brv._invalidation_reasons_for_leg(nhl_conn, leg, now_iso, now_iso, None)
    team_changed = [r for r in reasons if r.startswith("TEAM_CHANGED")]
    ok_team = (roster_team in (None, p["team"])) and not team_changed
    checks["identity_team"] = {"ok": ok_team, "product_team": p["team"], "roster_team": roster_team, "team_changed_alerts": team_changed}
    if not ok_team:
        findings.append(f"team mismatch: product {p['team']} vs roster {roster_team} / alerts {team_changed}")

    # small sample
    low = games < MIN_GAMES_FOR_PRICING
    ctx = leg_context.skater_context(p, {"market_family": "PLAYER_SOG_ALTERNATE"}) or {}
    in_options = pid in published_ids
    ok_sample = (not low) or (pr.get("pricing_eligible") is False and ctx.get("low_sample") is True and not in_options and "not a confident" in (ctx.get("uncertainty") or ""))
    if not low:
        ok_sample = pr.get("pricing_eligible") is True
    checks["sample_gate"] = {"ok": ok_sample, "games": games, "limited": low, "pricing_eligible": pr.get("pricing_eligible"), "in_published_options": in_options}
    if not ok_sample:
        findings.append(f"sample gate wrong at {games} games (eligible={pr.get('pricing_eligible')}, in options={in_options})")

    # explanation
    lines = ctx.get("lines") or []
    reported = p.get("reported") or {}
    has_reported_line = bool(reported.get("line") or reported.get("pp"))
    said_reported = any(l.startswith("Reported by") for l in lines)
    says_inferred = any("inferred" in l or "estimate" in l.lower() for l in lines[:1])
    ok_expl = bool(lines) and (said_reported == has_reported_line) and (says_inferred or not p.get("usage_tier"))
    checks["explanation"] = {"ok": ok_expl, "lines": len(lines), "reported_shown": said_reported, "reported_exists": has_reported_line, "usage_labelled_inferred": says_inferred}
    if not ok_expl:
        findings.append("explanation lines missing, or reported/inferred labelling wrong")

    # model sanity vs own recent rate
    rows = hist.get(pid, [])[-60:]
    sanity = {}
    probs = pr.get("probabilities") or {}
    for key, pred_key, fn in (("shots>=2", "shots>=2", lambda r: r["shots"] >= 2), ("points>=1", "points>=1", lambda r: r["goals"] + r["assists"] >= 1)):
        if len(rows) >= 20 and pred_key in probs:
            rate = sum(1 for r in rows if fn(r)) / len(rows)
            gap = probs[pred_key] - rate
            sanity[key] = {"model": probs[pred_key], "own_rate_last_60": round(rate, 3), "games": len(rows), "gap": round(gap, 3), "flag": abs(gap) > DISAGREE}
            if abs(gap) > DISAGREE:
                findings.append(f"{key}: model {probs[pred_key]:.0%} vs own last-{len(rows)} rate {rate:.0%}")
    checks["model_sanity"] = {"ok": not any(v["flag"] for v in sanity.values()), "detail": sanity}
    return {"player_id": pid, "name": p["name"], "stratum": stratum, "team": p["team"], "position": p.get("position"), "games": games,
            "usage_tier": p.get("usage_tier"), "next_game": f"{nxt.get('opp')} {nxt.get('date_et')}", "checks": checks, "findings": findings,
            "pass": all(c["ok"] for c in checks.values())}


def run(seed: int, per_stratum: int) -> dict:
    state = json.loads((RUNTIME / "product_state.json").read_text())
    players = state["players"]
    now_iso = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    published_ids = {str(l.get("participant_id")) for o in (state.get("options") or {}).get("options", []) for l in o.get("legs", [])}
    nhl_conn = db.get_conn()
    hist = _history()
    results = [audit_player(pid, label, players[pid], nhl_conn, hist, now_iso, published_ids) for pid, label in sample(players, seed, per_stratum)]
    return {"as_of_utc": now_iso, "product_generated_at_utc": state.get("generated_at_utc"), "seed": seed, "pricing_floor_games": MIN_GAMES_FOR_PRICING,
            "players_in_product": len(players), "sampled": len(results), "passed": sum(1 for r in results if r["pass"]),
            "published_option_players": len(published_ids), "results": results,
            "note": "Model-sanity flags are prompts to look, not failures: the model includes the opponent, ice time and calibration, which a 60-game rate does not."}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=20261009)
    ap.add_argument("--per-stratum", type=int, default=5)
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    report = run(a.seed, a.per_stratum)
    text = json.dumps(report, indent=1, default=str)
    if a.out:
        Path(a.out).write_text(text)
    print(f"sampled {report['sampled']}, all checks passed for {report['passed']}")
    for r in report["results"]:
        print(f"  {'ok  ' if r['pass'] else 'LOOK'} {r['name']:<24} {r['team']} {r['games']:>4} GP  [{r['stratum']}]" + ("" if r["pass"] else "  -- " + "; ".join(r["findings"])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
