"""Do the recency / role-risk flags (operational/eggy_intelligence.py recency_overlay) carry real signal? Read-only on the MoneyPuck logs already on disk; writes one file.

    python3 deploy/validate_overlay_signals.py --out docs/validation/overlay_signals.json

For each flag the question is the one the engine actually acts on: in the games AFTER the flag fires, does the player fall short of what his own longer record says? The comparison is each flagged game's outcome against the
player's own record over the games BEFORE the window the flag looked at (so the flag's evidence is not in its own baseline). A flag has SIGNAL when the shortfall is at least 2 standard errors; otherwise it is reported
as NO_DEMONSTRATED_SIGNAL and stays a caution only. One-game results are never the basis: every window is 3 or 5 games.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))


def _stat(n, sum_diff, sum_sq):
    if n < 2:
        return {"n": n}
    mean = sum_diff / n
    var = max(sum_sq / n - mean * mean, 1e-12)
    se = math.sqrt(var / n)
    return {"n": n, "mean_shortfall_vs_own_record": round(mean, 4), "se": round(se, 4), "z": round(mean / se, 2)}


def main() -> int:
    from research.product_models import history
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    rows = history.skater_games()
    by = defaultdict(list)
    for r in sorted(rows, key=lambda r: (r["player_id"], r["date"])):
        by[r["player_id"]].append(r)
    acc = {name: [0, 0.0, 0.0] for name in ("toi_drop_3.2_shots2", "toi_drop_3.2_points1", "toi_drop_4.0_shots2", "recent_form_0_or_1_of_5_shots2_players_at_55pct", "pp_loss_0.75_points1", "pp_loss_0.75_shots2")}

    def add(name, expected, actual):
        d = actual - expected                                   # negative = the flagged game fell short of the player's own record
        c = acc[name]
        c[0] += 1; c[1] += d; c[2] += d * d

    for pid, g in by.items():
        for i in range(40, len(g)):
            nxt = g[i]
            if nxt["toi"] < 8:
                continue
            base10 = g[i - 13:i - 3]
            l3 = g[i - 3:i]
            base_toi = sum(x["toi"] for x in base10) / len(base10)
            drop = base_toi - sum(x["toi"] for x in l3) / 3
            prior30 = g[i - 35:i - 5]                           # the record BEFORE the 5-game window (and before the 3-game one)
            r_s2 = sum(x["shots"] >= 2 for x in prior30) / len(prior30)
            r_p1 = sum(x["points"] >= 1 for x in prior30) / len(prior30)
            y_s2, y_p1 = int(nxt["shots"] >= 2), int(nxt["points"] >= 1)
            if base_toi >= 12:
                if drop >= 3.2:
                    add("toi_drop_3.2_shots2", r_s2, y_s2); add("toi_drop_3.2_points1", r_p1, y_p1)
                if drop >= 4.0:
                    add("toi_drop_4.0_shots2", r_s2, y_s2)
            l5 = g[i - 5:i]
            if r_s2 >= 0.55 and sum(x["shots"] >= 2 for x in l5) <= 1:
                add("recent_form_0_or_1_of_5_shots2_players_at_55pct", r_s2, y_s2)
            pp_base = sum(x["toi_pp"] for x in g[i - 13:i - 3]) / 10
            pp_l3 = sum(x["toi_pp"] for x in l3) / 3
            if pp_base >= 1.0 and pp_base - pp_l3 >= 0.75:
                add("pp_loss_0.75_points1", r_p1, y_p1); add("pp_loss_0.75_shots2", r_s2, y_s2)
    out = {"rows": len(rows), "method": __doc__.split("\n\n")[1].strip(), "signals": {}}
    for name, (n, sd, sq) in acc.items():
        st = _stat(n, sd, sq)
        st["verdict"] = "SIGNAL (flagged games fall short of the player's own record)" if st.get("z") is not None and st["z"] <= -2 else "NO_DEMONSTRATED_SIGNAL (stays a caution only)"
        out["signals"][name] = st
    Path(a.out).write_text(json.dumps(out, indent=1))
    for k, v in out["signals"].items():
        print(f"{k:55s} n {v['n']:>6} shortfall {v.get('mean_shortfall_vs_own_record')} z {v.get('z')}  {v['verdict'][:38]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
