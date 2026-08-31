"""Part 15: formats the end-of-run operational SYNC REPORT. Every number
shown comes directly from the actual sync results passed in — never a
fabricated count (Part: "do not use fabricated counts")."""
from __future__ import annotations


def format_sync_report(nhl_result: dict, moneypuck_result: dict | None,
                        crosscheck_result: dict | None, readiness: dict) -> str:
    lines = ["NHL DAILY SYNC", ""]

    lines.append("Schedule:")
    lines.append("PASS" if nhl_result.get("status") == "OK" else f"FAIL — {nhl_result.get('error')}")
    lines.append(f"{nhl_result.get('games_seen', 0)} games in window "
                 f"({nhl_result.get('window_start')}..{nhl_result.get('window_end')})")
    lines.append("")

    lines.append("Games finalized this run:")
    lines.append(str(nhl_result.get("games_finalized", 0)))
    lines.append("")

    lines.append("Roster sync:")
    lines.append(f"{nhl_result.get('teams_roster_synced', 0)} teams checked, "
                 f"{nhl_result.get('players_removed_this_pass', 0)} departures recorded")
    lines.append("")

    if moneypuck_result:
        for name in ("team", "skater", "goalie"):
            ds = moneypuck_result["datasets"].get(name)
            if not ds:
                continue
            lines.append(f"MoneyPuck {name}:")
            if ds["status"] == "NO_CHANGE":
                lines.append("NO_CHANGE")
            elif ds["status"] == "UPDATED":
                lines.append(f"UPDATED — archived to {ds.get('archived_path')}")
            elif ds["status"] == "REQUIRES_PERMISSION":
                lines.append("REQUIRES_PERMISSION — use local file ingestion")
            elif ds["status"] == "UNAVAILABLE":
                lines.append(f"UNAVAILABLE — {ds.get('reason')}")
            else:
                lines.append(ds["status"])
            lines.append("")

    if crosscheck_result:
        lines.append("NHL / MoneyPuck cross-check:")
        lines.append(f"{crosscheck_result['games_checked']} games checked, "
                     f"{crosscheck_result['matched']} matched, "
                     f"{crosscheck_result['known_shootout_exceptions']} known shootout exceptions, "
                     f"{len(crosscheck_result['material_disagreements'])} material disagreements")
        lines.append("")

    lines.append("Data readiness:")
    all_current = all(readiness[k]["status"] in ("CURRENT", "PROJECTED")
                       for k in ("nhl_schedule", "nhl_results", "moneypuck_team",
                                 "moneypuck_skater", "moneypuck_goalie", "starter_intelligence"))
    lines.append("READY" if all_current else "PARTIAL")
    for key in ("nhl_schedule", "nhl_results", "moneypuck_team", "moneypuck_skater",
                "moneypuck_goalie", "odds", "starter_intelligence"):
        lines.append(f"  {key}: {readiness[key]['status']}")

    return "\n".join(lines)
