# Simulation Invariants

Formal correctness invariants a future single-game joint simulation (Part 28) must satisfy. These
are **design requirements for future work**, not implemented or tested against real simulation
output — no simulator exists yet in this project. Each invariant is written so it can be turned
directly into a unit test once a simulator exists.

1. **Player goals sum to team statistical goals.** `sum(player.goals for player in team.roster) ==
   team.goals` for every team, every simulated game.
2. **Player SOG sums appropriately to team SOG.** `sum(player.sog for player in team.roster) ==
   team.sog`.
3. **Opponent team SOG reconciles with goalie shots faced**, per the accepted MoneyPuck/NHL
   convention already reused throughout this project: `goalie.shots_faced == opposing_team.sog`
   (adjusted for any recorded goalie change mid-game).
4. **Goalie saves + goalie goals allowed reconcile with shots faced.** `goalie.saves +
   goalie.goals_allowed == goalie.shots_faced`.
5. **Player points reconcile with goals/assists** — already a real, MoneyPuck-verified identity in
   this project (`I_F_points == I_F_goals + I_F_primaryAssists + I_F_secondaryAssists`, confirmed
   with zero mismatches across the full corpus, `PLAYER_POINTS_VALIDATION_REPORT.md` Section A). A
   future simulator must preserve this exactly, not approximate it.
6. **Period goals sum to regulation/OT totals**, under explicit semantics: `period_1_goals +
   period_2_goals + period_3_goals + (ot_goals if game_went_to_ot else 0) == team.goals`, where a
   shootout-deciding goal is explicitly excluded from `team.goals` under invariant 9.
7. **First scorer exists only when at least one statistical goal occurs.** `first_scorer is not
   None` if and only if `home.goals + away.goals >= 1` (counting only real, non-shootout goals).
8. **Last scorer corresponds to the last statistical goal**, i.e. the goal with the latest
   `(period, time_in_period)` ordering among all real (non-shootout) goals in the game.
9. **Shootout winner does not create an ordinary player goal.** A shootout-deciding tally must
   never increment `player.goals`, `team.goals`, or any period-goal total — it only decides
   `game.winner` and `game.method_of_victory`. This is the single most likely correctness bug a
   naive simulator would introduce, and is called out explicitly (Part 25).
10. **GWG follows NHL statistical definition** (Part 24): the game-winning goal is the goal that
    puts the eventual winning team one goal ahead of the losing team's **final** goal total — not
    simply "the last goal the winning team scored," and not derivable from a player's raw
    historical GWG rate alone. It can only be correctly labeled *after* the final score is known.
11. **Team/game totals reconcile.** `game.total_goals == home.goals + away.goals`; analogous sums
    hold for SOG, blocks, hits, PIM, and faceoffs at the game level.
12. **No negative event counts.** Every count field (goals, assists, points, SOG, blocks, hits,
    saves, PIM, faceoffs) is `>= 0` for every simulated player/team/game.
13. **No impossible threshold ordering.** For any coherent count distribution, `P(X >= n) >= P(X >=
    n+1)` for all `n` — already guaranteed *by construction* for every currently-validated prop
    model in this project via `research.player_sog.count_models.threshold_probabilities()` (shared,
    unmodified, reused by SOG/Blocks/Assists/Points/Goals); a future simulator's own threshold reads
    must preserve the same guarantee rather than re-deriving it independently per market.

Added by the Event-Timing Utility Closure slice (Part 25), after building and corpus-validating the
goalie-tenure and GWG research utilities in `research/real_nhl_pbp/{goalie_tenure,period_saves,gwg}.py`:

14. **Period goalie saves sum to full-game goalie saves.** `sum(goalie.saves_in_period[p] for p in
    periods) == goalie.saves` for every goalie who appeared in a game — guaranteed *by construction*
    in `period_saves.full_game_saves_by_goalie()` (it IS that sum, not a second computation), and
    confirmed with 0 coherence violations across the full 5,248-game corpus. A future simulator must
    preserve this the same way: compute period and full-game saves from ONE per-event goalie
    assignment, never two independent tallies that could silently drift apart.
15. **Every save belongs to the goalie actually in net for that event.** Never attributed to a
    team's nominal starter or to whichever goalie played the most that game — `event.players.get(
    "goalie")` (the same per-event field `normalize.py` already establishes) is the sole source of
    truth, confirmed correct across every real mid-period substitution and empty-net-then-return
    case examined this slice (`goalie_tenure.py`'s `RELIEF` vs `RETURN_AFTER_EMPTY_NET` distinction).
16. **An empty-net shot cannot generate a goalie save.** A shot-on-goal event with no `goalieInNetId`
    (the joint empty-net signal, Invariant 9's era) must never be credited as a save to any goalie —
    `period_saves.py` only ever counts a save when a real goalie identity is present on the event.
17. **GWG satisfies the final-score definition, not recency.** The game-winning goal is the winning
    team's `(losing_team_final_goals + 1)`-th statistical goal by event order — never simply the
    last goal, the go-ahead goal at the time it was scored, or an OT/empty-net goal by default.
    Confirmed on real corpus data where a later empty-net goal did NOT become the GWG (`gwg.py`,
    validated against game `2025020814`: the true GWG was the winning team's 6th goal, scored
    entirely at even strength in the 2nd period — two subsequent empty-net goals do not change it).
18. **A shootout attempt can never become a player's statistical GWG.** Confirmed on all 373 real
    shootout games in the 4-season corpus: the statistical score stays tied after regulation/OT for
    every one of them (the shootout-deciding goal is excluded from the statistical score per
    Invariant 9), so the final-score-based GWG definition has no winning team to assign a goal to —
    `gwg.derive_gwg()` returns `NO_PLAYER_GWG_SHOOTOUT`, never a fabricated skater GWG.
