"""PARLAY CAPACITY: the day's maximum is a ceiling, never an artifact. One selected ticket consumes exactly one slot; ten genuinely qualifying, diversified tickets fill ten slots; an eleventh never exists; a ticket the
gates refuse is never added because room remains; the wave caps still decide how many may be recorded NOW. Volume is an output of the intelligence: 0, 3, 5, 8 or 10."""
from __future__ import annotations

import datetime as dt
import random
import unittest
from unittest import mock

from operational import learning_cohort as lc
from operational import learning_parlays as lp
from tests.test_learning_cohort import Base, leg
from tests.test_learning_parlays import cand
from tests.test_portfolio_intelligence import with_evidence


def legs(n, seed=0, base=0.70, spread=0.01, games=None):
    r = random.Random(seed)
    return [with_evidence(cand(pid=str(8470001 + i), game=str((i % games) + 1) if games else str(i + 1), p=round(base - r.random() * spread, 4), team=f"T{i % (games or n)}")) for i in range(n)]


class TestOneTicketOneSlot(unittest.TestCase):
    def test_a_slot_count_is_the_number_of_tickets_selected(self):
        for slots in (1, 2, 5, 8, 10):
            out = lp.build(legs(30), slots=slots)
            self.assertEqual(len(out["parlays"]), slots, slots)

    def test_the_recorded_tickets_of_the_day_use_up_their_slots_first(self):
        base = lp.build(legs(30), slots=10)["parlays"]
        for k in (0, 3, 5, 9):
            already = [{"key": p["key"], "leg_keys": [c["key"] for c in p["cands"]], "view": lp.view_of_candidates(p)} for p in base[:k]]
            out = lp.build(legs(30), already, slots=10)
            self.assertEqual(len(out["parlays"]), 10 - k, k)                                       # recorded + new = ten, never five and never fewer than the room allows

    def test_ten_slots_are_reachable_and_the_pool_is_big_enough_to_reach_them(self):
        self.assertGreaterEqual(lp.POOL_LEGS * lp.LEG_REUSE, lp.MAX_PARLAYS_PER_DAY * 3)           # the exact minimum for ten three-leg tickets under the reuse rule
        for seed in range(8):
            out = lp.build(legs(30, seed), slots=lp.MAX_PARLAYS_PER_DAY)
            self.assertEqual(len(out["parlays"]), 10, seed)
            uses = {}
            for p in out["parlays"]:
                for c in p["cands"]:
                    uses[c["key"]] = uses.get(c["key"], 0) + 1
            self.assertLessEqual(max(uses.values()), lp.LEG_REUSE)                                   # the reuse cap of 2 holds at ten
            sets = [{c["key"] for c in p["cands"]} for p in out["parlays"]]
            self.assertTrue(all(len(a & b) <= 1 for i, a in enumerate(sets) for b in sets[i + 1:]))  # tickets still differ by two legs

    def test_an_eleventh_ticket_never_exists(self):
        out = lp.build(legs(40), slots=lp.MAX_PARLAYS_PER_DAY)
        self.assertEqual(len(out["parlays"]), 10)
        out = lp.build(legs(40), slots=25)                                                           # even if a caller asked for more, the recording side asks for the product's maximum
        self.assertLessEqual(len(out["parlays"]), 25)
        self.assertEqual(lc.daily_cap(), lp.MAX_PARLAYS_PER_DAY)
        base = lp.build(legs(40), slots=10)["parlays"]
        already = [{"key": p["key"], "leg_keys": [c["key"] for c in p["cands"]], "view": lp.view_of_candidates(p)} for p in base]
        self.assertEqual(lp.build(legs(40), already, slots=10)["parlays"], [])                      # ten recorded: nothing more


class TestVolumeIsAnOutputOfTheIntelligence(unittest.TestCase):
    def test_fewer_qualifying_legs_give_fewer_tickets_and_none_gives_none(self):
        self.assertEqual(lp.build([], slots=10)["parlays"], [])
        self.assertEqual(lp.build(legs(2), slots=10)["parlays"], [])
        n3 = len(lp.build(legs(4), slots=10)["parlays"])
        n8 = len(lp.build(legs(9), slots=10)["parlays"])
        self.assertLess(n3, 10)
        self.assertLess(n8, 10)
        self.assertLessEqual(n3, n8)

    def test_a_ticket_the_gates_refuse_is_never_added_because_room_remains(self):
        weak = [with_evidence(cand(pid=str(9000 + i), game=str(50 + i), p=0.50, team=f"W{i}")) for i in range(12)]        # under the 55% floor: not eligible legs
        flagged = [with_evidence(cand(pid=str(9100 + i), game=str(70 + i), p=0.70, team=f"F{i}", contradiction="HIGH", flags=[{"name": "ROLE_ASSUMPTION", "level": "HIGH", "text": "x"}])) for i in range(12)]
        self.assertEqual(lp.build(weak, slots=10)["parlays"], [])
        self.assertEqual(lp.build(flagged, slots=10)["parlays"], [])
        good = legs(6)
        out = lp.build(good + weak + flagged, slots=10)
        chosen_legs = {c["key"] for p in out["parlays"] for c in p["cands"]}
        self.assertFalse(chosen_legs & {c["key"] for c in weak + flagged})
        self.assertLess(len(out["parlays"]), 10)                                                    # the room was there; the tickets were not
        self.assertTrue(all(p["action"] == "RECORD" and p["construction_quality"] != "BAD" for p in out["parlays"]))

    def test_good_tickets_are_taken_before_questionable_ones_whatever_the_room(self):
        strong = legs(9, base=0.72, spread=0.004)
        thin = [with_evidence(cand(pid=str(9300 + i), game=str(90 + i), p=0.58 + 0.001 * i, team=f"Q{i}")) for i in range(9)]
        out = lp.build(strong + thin, slots=10)
        q = [p["construction_quality"] for p in out["parlays"]]
        self.assertEqual(q, sorted(q, key=lp.QUALITY.index))                                        # every GOOD precedes every QUESTIONABLE
        self.assertIn("GOOD", q)


NOV10 = dt.datetime(2026, 11, 10, 20, 0, tzinfo=dt.timezone.utc)                  # 15:00 ET: three hours before the first puck drop (no relaxed headroom yet)


class TestTheWaveCapsStillBind(Base):
    """The builder may choose ten; the cohort records only what the day's wave caps release NOW (operational/learning_cohort.py cumulative_caps)."""

    def setUp(self):
        super().setUp()
        p = mock.patch.object(lc, "UNIT", "PARLAY")
        p.start()
        self.addCleanup(p.stop)
        for g, hh, day in (("11", "23", "10"), ("12", "23", "10"), ("13", "03", "11"), ("14", "03", "11"), ("15", "03", "11"), ("16", "03", "11")):
            self.nhl.execute("INSERT INTO games (game_id, season, game_date, scheduled_start_utc, home_team, away_team, schedule_observed_at_utc, game_state, source) VALUES (?,?,?,?,?,?,?,?,?)",
                             (g, "20262027", "2026-11-10", f"2026-11-{day}T{hh}:00:00", "TOR", "MTL", "2026-11-10T00:00:00", "SCHEDULED", "test"))
        self.nhl.commit()

    def pool(self, games, n):
        out = []
        for i in range(n):
            g = games[i % len(games)]
            hh, day = ("23", "10") if g in ("11", "12") else ("03", "11")
            out.append(leg(pid=str(8470001 + i), game=g, p=0.70 - 0.001 * i, price=-130.0, team=f"T{i}", game_start_utc=f"2026-11-{day}T{hh}:00:00", provider_start_utc=f"2026-11-{day}T{hh}:00:00Z"))
        return out

    def test_the_caps_release_slots_in_proportion_to_the_games_played_through_each_wave(self):
        game_wave, total, starts = lc.day_waves(self.nhl, "2026-11-10")
        caps = lc.cumulative_caps(game_wave, total, starts, dt.datetime(2026, 11, 10, 15, 0, tzinfo=dt.timezone.utc))
        self.assertEqual(sorted(set(game_wave.values())), [0, 1])
        self.assertEqual(caps[0], 4)                                                                 # 2 of 6 games: ceil(10 * 2 / 6) = 4 slots by the first wave
        self.assertEqual(caps[1], 10)                                                                # the last wave releases the product's maximum
        self.assertEqual(max(lc.cumulative_caps(game_wave, total).values()), lc.PARLAY_DAILY_MAX)

    def test_a_builder_that_could_choose_ten_records_only_what_the_first_wave_may_hold(self):
        self.cycle(self.pool(["11", "12"], 24), now=NOV10)
        n = self.conn.execute("SELECT COUNT(*) FROM parlays WHERE et_date = '2026-11-10'").fetchone()[0]
        self.assertGreater(n, 0)
        self.assertLessEqual(n, 4)                                                                   # the wave-0 cap, not the builder's ten
        self.cycle(self.pool(["11", "12"], 24), now=NOV10 + dt.timedelta(minutes=10))
        self.assertLessEqual(self.conn.execute("SELECT COUNT(*) FROM parlays WHERE et_date = '2026-11-10'").fetchone()[0], 4)      # and it does not creep past it on the next cycle

    def test_the_last_wave_can_use_the_whole_maximum_and_never_more(self):
        self.cycle(self.pool(["13", "14", "15", "16"], 36), now=NOV10)
        n = self.conn.execute("SELECT COUNT(*) FROM parlays WHERE et_date = '2026-11-10'").fetchone()[0]
        self.assertGreater(n, 4)
        self.assertLessEqual(n, 10)
        for k in range(1, 4):
            self.cycle(self.pool(["13", "14", "15", "16"], 36), now=NOV10 + dt.timedelta(minutes=10 * k))
        self.assertLessEqual(self.conn.execute("SELECT COUNT(*) FROM parlays WHERE et_date = '2026-11-10'").fetchone()[0], 10)    # never an eleventh across cycles


if __name__ == "__main__":
    unittest.main()
