"""
Odds API quota guard, reset-date status and forward projection (Quota + Moneyline Activation block,
2026-09-25). Pure functions plus two thin readers of already-cached local state -- this module makes
NO network call and spends NO credit.

The guard preserves the same policy the daily pull already used (monthly hard cap via the account's real
`x-requests-remaining`, a fixed safety reserve, days left in the cycle, a daily soft budget) so every
paid job asks one question the same way: "may I spend N credits now?"
"""
from __future__ import annotations

import datetime as dt
import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

PLAN_MONTHLY_CREDITS = 500          # 132 used + 368 remaining observed 2026-09-25 (x-requests-used / -remaining)
RESERVE = 20                        # never spend the account to zero (== live_odds_daily_pull.DEFAULT_SAFETY_FLOOR)
RESET_DAY_ENV = "NHL_ENGINE_ODDS_RESET_DAY"
DEFAULT_RESET_DAY = 1               # ASSUMPTION until the owner verifies it on the provider account
# The pregame moneyline pull is THE product's decision feed, so it may borrow ahead of the even daily
# pace (a soft budget) but is still bounded by the hard reserve.
PREGAME_SOFT_MULTIPLIER = 3.0


def _env_value(name: str) -> str | None:
    value = (os.environ.get(name) or "").strip()
    if value:
        return value
    env_file = REPO_ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            key, sep, val = line.strip().partition("=")
            if sep and key.strip() == name:
                return val.strip()
    return None


def reset_status() -> dict:
    """The provider exposes no reset date in any response header or account endpoint observed so far
    (checked: x-requests-used / -remaining / -last only). Never invented: unless the owner sets
    NHL_ENGINE_ODDS_RESET_DAY (an integer 1-28, from the provider account dashboard) the status is
    OWNER_VERIFICATION_REQUIRED and the calendar-month assumption is labeled an assumption. A present but
    invalid value (not an integer, or outside 1-28) is reported as INVALID and treated as unset -- it never
    silently becomes a reset day."""
    configured = _env_value(RESET_DAY_ENV)
    if configured is None or configured == "":
        return {"status": "OWNER_VERIFICATION_REQUIRED", "reset_day": DEFAULT_RESET_DAY, "assumed": True, "invalid": False}
    text = configured.strip().strip("\"'")
    if text.isdigit() and 1 <= int(text) <= 28:
        return {"status": "OWNER_CONFIGURED", "reset_day": int(text), "assumed": False, "invalid": False}
    return {"status": "OWNER_VERIFICATION_REQUIRED", "reset_day": DEFAULT_RESET_DAY, "assumed": True, "invalid": True,
            "error": f"{RESET_DAY_ENV} must be an integer 1-28 (got a value that is not)"}


def days_left_in_cycle(today: dt.date, reset_day: int | None = None) -> int:
    reset_day = reset_day or reset_status()["reset_day"]
    if today.day < reset_day:
        nxt = today.replace(day=reset_day)
    else:
        year, month = (today.year + 1, 1) if today.month == 12 else (today.year, today.month + 1)
        nxt = dt.date(year, month, min(reset_day, 28))
    return max((nxt - today).days, 1)


def evaluate_spend(remaining: int | None, spent_today: int, days_left: int, planned: int = 1, *,
                   reserve: int = RESERVE, soft_multiplier: float = 1.0) -> dict:
    """ALLOW / DEFER for a planned spend of `planned` credits.

    Hard cap: never dip into the reserve (`remaining - planned < reserve`). Soft budget: the even daily
    pace `(remaining - reserve) / days_left`, scaled by `soft_multiplier` for high-priority jobs. Unknown
    remaining quota (`None`) defers -- an unknown balance is never treated as sufficient."""
    if remaining is None:
        return {"allow": False, "reason": "QUOTA_UNKNOWN", "daily_soft_budget": None}
    usable = max(remaining - reserve, 0)
    soft = (usable / max(days_left, 1)) * soft_multiplier
    if remaining - planned < reserve:
        return {"allow": False, "reason": "HARD_RESERVE", "daily_soft_budget": round(soft, 1),
                "remaining": remaining, "reserve": reserve}
    if spent_today + planned > max(soft, planned):
        return {"allow": False, "reason": "DAILY_SOFT_BUDGET", "daily_soft_budget": round(soft, 1),
                "remaining": remaining, "spent_today": spent_today}
    return {"allow": True, "reason": "OK", "daily_soft_budget": round(soft, 1), "remaining": remaining,
            "spent_today": spent_today}


def _as_utc(stamp) -> dt.datetime | None:
    try:
        t = dt.datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)


CONCURRENT_WINDOW_MIN = 15       # responses this close together may arrive out of order: the lowest balance among them is the safe one


def balance_detail(now: dt.datetime | None = None) -> dict:
    """The credit balance the spending guard should believe, and how it was reached.

    BASELINE   the newest provider `x-requests-remaining` anything recorded: the collectors' cached summary or a settled ledger row (which carries the header of the call that was charged).
               Responses within CONCURRENT_WINDOW_MIN of the newest may have arrived out of order, so the LOWEST of them is used (a balance can only fall inside a cycle; too low is safe).
    DEDUCTIONS every ledger row WITHOUT its own header, stamped after the baseline: a charge the baseline cannot have seen. A row stamped at or before the baseline is already inside it, and a
               row with its own header IS a baseline: nothing is counted twice, and the next response replaces every deduction.
    Reservations still pending are not here: reserve() subtracts them itself. Unreadable or future-dated rows are skipped, never fatal."""
    from operational import system_health as sh
    now = now or dt.datetime.now(dt.timezone.utc)
    points: list[tuple[dt.datetime, int, str]] = []
    try:
        status = sh.odds_collection_status()
        value = status.get("credits_remaining")
        when = _as_utc(status.get("last_updated_utc"))
        if isinstance(value, (int, float)) and not isinstance(value, bool) and when is not None:
            points.append((when, int(value), "collector cache"))
    except Exception:  # noqa: BLE001
        pass
    rows: list[dict] = []
    try:
        from operational import credit_planner as cp
        rows = cp.read_ledger()
    except Exception:  # noqa: BLE001
        cp = None
    unheadered: list[tuple[dt.datetime, float]] = []
    for row in rows:
        try:
            t = _as_utc(row.get("at"))
            if t is None or t > now + dt.timedelta(minutes=5):
                continue
            h = cp.parse_header(row.get("remaining_header"))
            if h is not None:
                points.append((t, h, "ledger header"))
            else:
                unheadered.append((t, float(row.get("credits") or 0.0)))
        except Exception:  # noqa: BLE001
            continue
    if not points:
        return {"remaining": None, "baseline_value": None, "baseline_at": None, "deducted": 0.0, "source": None}
    newest = max(p[0] for p in points)
    near = [p for p in points if p[0] >= newest - dt.timedelta(minutes=CONCURRENT_WINDOW_MIN)]
    base_t, base_v, source = min(near, key=lambda p: (p[1], -p[0].timestamp()))
    deducted = sum(c for t, c in unheadered if t > newest)
    return {"remaining": max(int(base_v - deducted), 0), "baseline_value": base_v, "baseline_at": base_t.strftime("%Y-%m-%dT%H:%M:%SZ"), "deducted": deducted, "source": source}


def latest_remaining() -> int | None:
    """The balance the spending guard uses (never a guess): the newest real provider header minus every charge recorded after it (see balance_detail)."""
    return balance_detail()["remaining"]


def credits_spent_today(now: dt.datetime | None = None) -> int:
    """Real credits charged since 00:00 UTC, from the archived responses' own `requests_last` headers."""
    from operational import live_odds_daily_pull as lop
    now = now or dt.datetime.now(dt.timezone.utc)
    midnight = dt.datetime.combine(now.date(), dt.time.min, dt.timezone.utc)
    return int(lop._credits_spent_since(midnight))


def guard(planned: int = 1, *, now: dt.datetime | None = None, soft_multiplier: float = 1.0,
          remaining: int | None = None, spent_today: int | None = None) -> dict:
    now = now or dt.datetime.now(dt.timezone.utc)
    remaining = latest_remaining() if remaining is None else remaining
    spent = credits_spent_today(now) if spent_today is None else spent_today
    return evaluate_spend(remaining, spent, days_left_in_cycle(now.date()), planned, soft_multiplier=soft_multiplier)


def project(remaining: int, daily_burn: float, *, days_to_reset: int | None = None) -> dict:
    """Forward projection: days until exhaustion (to the reserve) at `daily_burn` credits/day, and whether
    the balance lasts until a reset `days_to_reset` days away (None = unknown -> only the runway is stated)."""
    usable = max(remaining - RESERVE, 0)
    runway = None if daily_burn <= 0 else round(usable / daily_burn, 1)
    out = {"daily": round(daily_burn, 1), "weekly": round(daily_burn * 7, 1), "days_until_exhaustion": runway}
    if days_to_reset is not None and runway is not None:
        out["lasts_until_reset"] = runway >= days_to_reset
        out["left_at_reset"] = max(round(usable - daily_burn * days_to_reset, 0), 0) + RESERVE
    return out
