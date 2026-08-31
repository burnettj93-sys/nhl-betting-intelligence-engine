"""
Part 21/22: the prospective recording entry point for the SOG PP-role
shadow overlay. Computes BOTH the frozen production SOG prediction
(completely unchanged -- research.player_sog.live_projection.project_player_sog,
called exactly as production already calls it) and the shadow, role-
adjusted prediction (operational.sog_shadow_overlay), and records both
side by side via the CANONICAL operational.prospective_recording.record_observation()
entry point (Preseason Engine Freeze sprint, 2026-08-30, Part 1 fix --
this module previously called operational.prospective_ledger.record_model_observation()
directly, silently bypassing the checkpoint-ordering guard the Preseason
Operational Readiness Closure sprint added: a PRE_GAME_UPDATE/MARKET_REFRESH
shadow observation could have been recorded with no PRIMARY_DAILY ever
having existed for that same logical bet. Routing through the canonical
path closes that gap without changing any of what gets recorded --
MODEL_OBSERVATION record type -- Part 19: the shadow value NEVER
becomes a REAL_BET, and this function never calls record_real_bet).

Records even when no sportsbook market exists (Part 22): market-price
columns are simply left None/NULL, exactly like every other
MODEL_OBSERVATION already does.
"""
from __future__ import annotations

from operational import prospective_recording as pr
from operational import sog_shadow_overlay as shadow
from operational import special_teams_history_store as sths
from operational import special_teams_roles_live as srl


def record_sog_observation(conn, ledger_conn, *, player_id: str, team: str, opponent: str,
                            game_id: str, game_date: str, event_start_utc: str,
                            prediction_cutoff_utc: str, season: int, threshold: str = "3+",
                            prediction_checkpoint: str = "PRIMARY_DAILY",
                            model_version: str = "", is_demo: bool = False) -> dict:
    """`conn`: the real, frozen SOG live-projection context (must expose
    the same interface as research.player_sog.live_projection.project_player_sog's
    positional args, or a wrapper -- passed in rather than constructed
    here so this function never re-implements the frozen model's own
    setup). `ledger_conn`: an operational.prospective_ledger connection.

    Returns {"status": "RECORDED"/"DEMO_NOT_RECORDABLE"/"SKIPPED", ...}.
    Mirrors operational/prospective_recording.py's own DEMO_NOT_RECORDABLE
    guard -- checked FIRST, before any other logic (Part: this project's
    own established, tested pattern)."""
    if is_demo:
        return {"status": "DEMO_NOT_RECORDABLE"}

    threshold_int = int(threshold.rstrip("+"))
    frozen = conn.predict(player_id, team, opponent, game_date, season)
    if frozen is None:
        return {"status": "SKIPPED", "reason": "frozen model returned no prediction "
                                                 "(PROJECTED_INACTIVE / INSUFFICIENT_HISTORY)"}
    raw_probability = frozen["probs"].get(threshold_int)
    if raw_probability is None:
        return {"status": "SKIPPED", "reason": f"no frozen probability for threshold {threshold}"}

    sth_conn = sths.get_connection()
    role_state = srl.compute_pp_role_state(sth_conn, player_id, team, event_start_utc[:10])

    coefficients = shadow.load_frozen_coefficients()
    alpha = getattr(conn, "alpha", None)
    shadow_result = shadow.compute_shadow_sog(frozen["mu"], alpha, role_state, coefficients)
    shadow_prob = shadow_result["shadow_probs"].get(threshold_int)

    prediction = dict(
        model_id="PLAYER_SOG",
        event_start_utc=event_start_utc, created_at_utc=prediction_cutoff_utc,
        prediction_cutoff_utc=prediction_cutoff_utc, game_id=game_id, game_date=game_date,
        player_id=player_id, team=team, opponent=opponent, market_id="PLAYER_SOG",
        market_family="SOG", threshold=threshold, raw_probability=raw_probability,
        conservative_probability=raw_probability,  # production conservative path unchanged/untouched here
        model_version=model_version,
        sog_shadow_raw_probability=shadow_prob,
        sog_shadow_conservative_probability=shadow_prob,  # conservative shrinkage applied on the
                                                            # count scale INSIDE compute_shadow_sog
                                                            # already (Part 17) -- no second shrink here
        pp_role_state=role_state["state"], pp_role_certainty=shadow_result["certainty"],
        pp_transition_state=role_state["state"] if role_state.get("games_since_onset") is not None else None,
        pp_games_since_transition=role_state.get("games_since_onset"),
        role_overlay_version=shadow.OVERLAY_VERSION,
    )
    # Canonical path (Part 1 fix): enforces PRIMARY_DAILY-before-PRE_GAME_UPDATE/
    # MARKET_REFRESH ordering (raises CheckpointOrderingError otherwise), and
    # confirms PLAYER_SOG's real MODEL_REGISTRY eligibility on every call --
    # neither existed when this called pl.record_model_observation() directly.
    result = pr.record_observation(ledger_conn, prediction, is_demo=False, checkpoint=prediction_checkpoint)
    return {"status": "RECORDED", "ledger_result": result, "raw_probability": raw_probability,
            "shadow_probability": shadow_prob, "role_state": role_state["state"]}
