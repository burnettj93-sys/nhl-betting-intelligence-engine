"""
The morning update (launchd, 08:00 Eastern): the day's first look, so the hosted app is populated from the morning rather than from mid-afternoon.

Order (each step is independent; one failing never stops the next, and every outcome is written down):
  1. statistics   the 07:00 sync has already refreshed schedule, results and player logs; this step only RECORDS how fresh they are (data-through dates).
  2. moneyline    the day's first league-wide moneyline pull, if due (one credit; it also carries tomorrow's early lines). Before 08:00 the planner refuses it, so the
                  first display refresh of the day is this one, not a midnight pull that leaves the morning on last night's price.
  3. cycle        one run of the 15-minute trader: the MORNING capture of DraftKings player prices for every game the credit plan covers (operational/capture_schedule.py),
                  provisional options and provisional tickets, and publication to the hosted app. A morning price that is fresh can be recorded on an automatic ticket, but at most
                  daily_tickets.EARLY_TICKET_CAP tickets a day may rest on early prices, and each is re-judged on the clock at the moment of recording.
The 15-minute trader keeps looking after this (a market DraftKings has not posted yet is asked again hourly), so a late or missed 08:00 run is caught by the next cycle,
and the watchdog warns from 08:45 and fails from 10:00 if no game was looked at.
"""
from __future__ import annotations

import datetime as dt
import json

from operational import state_paths

STATE_NAME = "morning_update_state.json"


def _write(doc: dict) -> None:
    try:
        p = state_paths.path(STATE_NAME)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(doc, indent=1, sort_keys=True, default=str))
        tmp.replace(p)
    except OSError:
        pass


def read() -> dict | None:
    try:
        return json.loads(state_paths.path(STATE_NAME).read_text())
    except (OSError, json.JSONDecodeError):
        return None


def _statistics(now: dt.datetime) -> dict:
    try:
        d = json.loads(state_paths.path("product_state.json").read_text())
        return {"status": "RECORDED", "data_through": d.get("data_through"), "product_generated_at_utc": d.get("generated_at_utc")}
    except (OSError, json.JSONDecodeError):
        return {"status": "NO_PRODUCT_STATE"}


def run(now: dt.datetime | None = None) -> dict:
    from operational import eastern_time as et, price_availability as pa
    now = now or dt.datetime.now(dt.timezone.utc)
    doc = {"day": et.eastern_today(now), "started_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "steps": {}}
    try:
        from operational import deployment_mode as dm
        if not dm.is_active_scheduler():
            doc.update(status="SKIPPED", reason="STANDBY")
            _write(doc)
            return doc
    except Exception:  # noqa: BLE001
        pass
    doc["steps"]["statistics"] = _statistics(now)
    try:
        from operational import moneyline_freshness
        r = moneyline_freshness.run_if_due(now, label="morning")
        doc["steps"]["moneyline"] = {k: r.get(k) for k in ("action", "reason", "ran", "credits_spent_this_run", "api_error")}
    except Exception as exc:  # noqa: BLE001
        doc["steps"]["moneyline"] = {"action": "ERROR", "reason": f"{exc.__class__.__name__}: {exc}"}
    try:
        from operational import real_parlay_paper_trader
        res = real_parlay_paper_trader.run(now)
        cap = ((res.get("price_refresh") or {}).get("capture")) or {}
        doc["steps"]["cycle"] = {"status": "RAN", "events_captured": cap.get("events_captured"), "credits_spent": cap.get("credits_spent"), "slots": cap.get("slots"),
                                 "tomorrow": cap.get("tomorrow"), "newly_recorded": (res.get("stake_result") or {}).get("newly_recorded"),
                                 "published": bool(res.get("cloud_publish"))}
    except Exception as exc:  # noqa: BLE001
        doc["steps"]["cycle"] = {"status": "ERROR", "reason": f"{exc.__class__.__name__}: {exc}"}
    doc["morning_status"] = pa.morning_status(dt.datetime.now(dt.timezone.utc))
    doc["finished_utc"] = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    doc["status"] = "DONE" if all(s.get("status") != "ERROR" and s.get("action") != "ERROR" for s in doc["steps"].values()) else "PARTIAL"
    _write(doc)
    return doc


if __name__ == "__main__":
    print(json.dumps(run(), indent=2, default=str))
