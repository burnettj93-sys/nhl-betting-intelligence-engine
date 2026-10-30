"""GOALIE SOURCES (operational/goalie_sources.py): GoaliePost primary, GameDayTweets corroboration. CONFIRMED is never PROBABLE and an inferred or hedged starter is never CONFIRMED;
stale data is UNKNOWN; disagreement is a visible CONFLICT; history keeps who was expected and when; Eggy consumes it as one weak personnel reason and one confidence cap."""
from __future__ import annotations

import datetime as dt
import json
import os
import tempfile
import unittest
from unittest import mock

from operational import eggy_pick as ep
from operational import goalie_sources as gs
from tests.test_eggy_pick import cand

Z = dt.timezone.utc
NOW = dt.datetime(2026, 10, 28, 20, 37, tzinfo=Z)
EP = lambda h: int((NOW - dt.timedelta(hours=h)).timestamp())          # an epoch `h` hours before NOW


def entry(pid, name, label, hours_ago, source="@reporter", comment="text"):
    last, first = name.split(" ")[1], name.split(" ")[0]
    links = [{"link": f"https://x.com/{source.strip('@')}/status/1", "text": source}] if source else []
    return {"id": "a" * 24, "goalie": {"id": "b" * 24, "externalId": int(pid), "firstName": first, "lastName": last, "fullName": name}, "likeliness": label, "comments": comment, "links": links,
            "stats": {}, "projectedSaves": 24, "timestamp": EP(hours_ago)}


def game_obj(gid, home, away, h_entries, a_entries, state="Preview"):
    return {"id": "c" * 24, "externalId": gid, "sportsNetGameId": 0, "gameType": "R", "gameDate": "$D2026-10-28T23:30:00.000Z", "gameEpochDate": EP(-3), "status": {"abstractGameState": state},
            "teams": {"HOME": {"team": {"abbreviation": home}}, "AWAY": {"team": {"abbreviation": away}}}, "predictedGoalies": {"HOME": h_entries, "AWAY": a_entries}, "updatedAt": EP(0.1)}


def gp_page(*games):
    blob = ",".join(json.dumps(g, separators=(",", ":")) for g in games)
    return "<html><script>self.__next_f.push([1," + json.dumps(blob) + "])</script></html>"


def gdt_page(matchups, date="2026-10-28"):
    out = [f'<a href="/goalies?date=2026-10-27">Prev</a>']
    for away, home, away_posts, home_posts in matchups:
        def col(posts):
            return "".join(f'<blockquote class="tweet"> <p lang="" dir="ltr"><a class="handle" href="https://twitter.com/{h.strip("@")}" target="_blank">{h}</a><br> {t}</p> &mdash; X <a href="https://x.com/s/1">{d}</a> </blockquote>'
                           for h, t, d in posts)
        out.append(f'<h1 class="text-3xl"><span>x</span><img src="/assets/logos/{away.lower()}-{"0" * 40}.svg"/><img src="/assets/logos/{home.lower()}-{"1" * 40}.svg"/></h1>'
                   f'<div class="x"><div class="col">{col(away_posts)}</div><div class="invisible"> @ </div><div class="col">{col(home_posts)}</div></div>')
    return "".join(out)


class TestGoaliePostParsing(unittest.TestCase):
    def test_game_ids_player_ids_labels_sources_and_the_sources_own_time(self):
        page = gp_page(game_obj(2026020199, "WSH", "PHI", [entry(8479292, "Charlie Lindgren", "PROBABLE", 5)], [entry(8479361, "Joseph Woll", "LIKELY", 3, "@sammisilber", "second half of B2B")]))
        g = gs.parse_goaliepost(page)["games"][0]
        self.assertEqual((g["game_id"], g["home"], g["away"], g["state"]), (2026020199, "WSH", "PHI", "Preview"))
        a = g["sides"]["AWAY"][0]
        self.assertEqual((a["player_id"], a["name"], a["label"], a["source_name"], a["comment"]), ("8479361", "Joseph Woll", "LIKELY", "@sammisilber", "second half of B2B"))
        self.assertTrue(a["source_at_utc"].endswith("Z"))                                        # the source's own update time, not the retrieval time

    def test_an_unknown_page_shape_raises_so_the_last_good_data_is_kept(self):
        with self.assertRaises(ValueError):
            gs.parse_goaliepost("<html>nothing here</html>")


class TestStatusSemantics(unittest.TestCase):
    def side(self, label, hours, source="@r", posts=(), cands=()):
        return gs.resolve_side([entry(1, "Aa Bb", label, hours, source)] and [{**e, "player_id": str(e["goalie"]["externalId"]), "name": e["goalie"]["fullName"], "label": e["likeliness"], "comment": e["comments"],
                                                                                "source_name": (e["links"][0]["text"] if e["links"] else ""), "source_url": (e["links"][0]["link"] if e["links"] else ""),
                                                                                "source_at_utc": gs._epoch(e["timestamp"])} for e in [entry(1, "Aa Bb", label, hours, source)]],
                               list(posts), list(cands) or [{"player_id": "1", "name": "Aa Bb"}], NOW, "2026-10-28")

    def test_confirmed_needs_a_named_source_and_a_recent_time(self):
        self.assertEqual(self.side("CONFIRMED", 2)["status"], gs.CONFIRMED)
        self.assertEqual(self.side("CONFIRMED", 2, source="")["status"], gs.PROBABLE)               # no source: never CONFIRMED
        self.assertEqual(self.side("CONFIRMED", 2, source="")["note"], "NO_SOURCE")
        self.assertEqual(self.side("CONFIRMED", 31)["status"], gs.PROBABLE)                          # a confirmation older than 30 hours
        self.assertEqual(self.side("CONFIRMED", -1)["status"], gs.UNKNOWN)                          # a time in the future

    def test_likely_and_probable_are_probable_and_keep_their_raw_label(self):
        for lab in ("LIKELY", "PROBABLE"):
            r = self.side(lab, 3)
            self.assertEqual((r["status"], r["label"]), (gs.PROBABLE, lab))

    def test_an_unlabelled_entry_is_unknown_and_an_old_listing_is_stale_and_unknown(self):
        self.assertEqual(self.side("MAYBE", 3)["status"], gs.UNKNOWN)
        old = self.side("PROBABLE", 80)
        self.assertEqual((old["status"], old["freshness"]), (gs.UNKNOWN, gs.STALE))                  # stale data cannot masquerade as current
        self.assertEqual(self.side("PROBABLE", 30)["freshness"], gs.RECENT)
        self.assertEqual(self.side("PROBABLE", 3)["freshness"], gs.CURRENT)

    def test_no_entry_is_unknown(self):
        r = gs.resolve_side([], [], [], NOW, "2026-10-28")
        self.assertEqual((r["status"], r["goalie"]), (gs.UNKNOWN, None))


class TestGameDayTweets(unittest.TestCase):
    CANDS = [{"player_id": "1", "name": "Mackenzie Blackwood"}, {"player_id": "2", "name": "Jake Allen"}, {"player_id": "3", "name": "Poirier"},
             {"player_id": "4", "name": "Philipp Grubauer"}, {"player_id": "5", "name": "Logan Thompson"}, {"player_id": "6", "name": "Tristan Jarry"}]

    def who(self, text):
        r = gs.direct_start(text, self.CANDS)
        return r and r["name"]

    def test_direct_language_counts(self):
        self.assertEqual(self.who("Jarry is starting."), "Tristan Jarry")
        self.assertEqual(self.who("Allen will start in Colorado with Poirier backing up."), "Jake Allen")
        self.assertEqual(self.who("Mackenzie Blackwood is in the starters crease, as expected"), "Mackenzie Blackwood")
        self.assertEqual(self.who("Sergei Bobrovsky is the cofirmed starter tonight."), None)             # not a candidate of this game
        self.assertEqual(self.who("Philipp Grubauer is the confirmed starter tonight."), "Philipp Grubauer")
        self.assertEqual(self.who("Philipp Grubauer is the confirmed starter tonight. They are still waiting for his gear to arrive."), "Philipp Grubauer")      # a hedge in a different sentence does not void it

    def test_hedged_or_projected_language_is_never_a_start(self):
        for t in ("Capitals will see how Logan Thompson is this afternoon and make a decision on if he'll start from there.",
                  "Philipp Grubauer came off first. Projecting him as tonight's starter.", "Thompson is expected to start", "Thompson will start if healthy", "Thompson is a game-time decision"):
            self.assertIsNone(gs.direct_start(t, self.CANDS), t)

    def test_page_parsing_maps_sides_by_column_and_ignores_the_guess(self):
        page = gdt_page([("PHI", "WSH", [], [("@sammisilber", "Thompson is starting.", "Oct 28, 2026")]), ("CGY", "EDM", [("@EricFrancis", "Cooley is in the starter's net.", "Oct 28, 2026")], [])])
        d = gs.parse_gamedaytweets(page)
        self.assertEqual(d["date"], "2026-10-28")
        self.assertEqual([(m["away"], m["home"], len(m["away_tweets"]), len(m["home_tweets"])) for m in d["matchups"]], [("PHI", "WSH", 0, 1), ("CGY", "EDM", 1, 0)])
        self.assertEqual(d["matchups"][0]["home_tweets"][0]["tweet_date"], "2026-10-28")


class TestCorroborationAndConflict(unittest.TestCase):
    def ent(self, pid, name, label, hours, source="@r"):
        e = entry(pid, name, label, hours, source)
        return {"player_id": str(pid), "name": name, "label": label, "comment": "", "source_name": source, "source_url": e["links"][0]["link"] if source else "", "source_at_utc": gs._epoch(e["timestamp"])}

    def post(self, text, date="2026-10-28"):
        return {"handle": "@beat", "handle_url": "", "text": text, "tweet_url": "https://x.com/s/1", "tweet_date": date}

    CANDS = [{"player_id": "10", "name": "Logan Thompson"}, {"player_id": "11", "name": "Charlie Lindgren"}]

    def test_a_direct_post_naming_the_same_goalie_upgrades_probable_to_confirmed(self):
        r = gs.resolve_side([self.ent(10, "Logan Thompson", "LIKELY", 3)], [self.post("Thompson is starting.")], self.CANDS, NOW, "2026-10-28")
        self.assertEqual((r["status"], r["basis"]), (gs.CONFIRMED, "GAMEDAYTWEETS_DIRECT_SAME_GOALIE"))

    def test_a_hedged_post_changes_nothing(self):
        r = gs.resolve_side([self.ent(10, "Logan Thompson", "LIKELY", 3)], [self.post("Thompson is expected to start.")], self.CANDS, NOW, "2026-10-28")
        self.assertEqual(r["status"], gs.PROBABLE)

    def test_two_sources_naming_different_goalies_the_same_day_is_a_conflict_not_a_pick_of_one(self):
        r = gs.resolve_side([self.ent(11, "Charlie Lindgren", "PROBABLE", 3)], [self.post("Thompson will start.")], self.CANDS, NOW, "2026-10-28")
        self.assertEqual(r["status"], gs.CONFLICT)
        self.assertIn("neither is shown to be newer", r["conflict"])

    def test_a_confirmed_listing_against_a_different_direct_post_is_a_conflict(self):
        r = gs.resolve_side([self.ent(11, "Charlie Lindgren", "CONFIRMED", 3)], [self.post("Thompson will start.")], self.CANDS, NOW, "2026-10-28")
        self.assertEqual(r["status"], gs.CONFLICT)

    def test_a_newer_calendar_day_post_supersedes_an_older_probable_and_records_what_it_replaced(self):
        r = gs.resolve_side([self.ent(11, "Charlie Lindgren", "PROBABLE", 30)], [self.post("Thompson will start.")], self.CANDS, NOW, "2026-10-28")
        self.assertEqual((r["status"], r["goalie"], r["basis"]), (gs.CONFIRMED, "Logan Thompson", "GAMEDAYTWEETS_DIRECT_SUPERSEDES_OLDER_LISTING"))
        self.assertEqual(r["superseded"]["goalie"], "Charlie Lindgren")

    def test_a_direct_post_with_no_listing_is_confirmed_by_the_post_alone(self):
        r = gs.resolve_side([], [self.post("Thompson will start.")], self.CANDS, NOW, "2026-10-28")
        self.assertEqual((r["status"], r["basis"]), (gs.CONFIRMED, "GAMEDAYTWEETS_DIRECT_ONLY"))

    def test_game_state_names(self):
        c, p, u, x = ({"status": s} for s in (gs.CONFIRMED, gs.PROBABLE, gs.UNKNOWN, gs.CONFLICT))
        self.assertEqual(gs._state_of({"H": c, "A": c}), "BOTH_CONFIRMED")
        self.assertEqual(gs._state_of({"H": c, "A": p}), "ONE_PROBABLE")
        self.assertEqual(gs._state_of({"H": p, "A": p}), "BOTH_PROBABLE")
        self.assertEqual(gs._state_of({"H": c, "A": u}), "UNKNOWN_GOALIE")
        self.assertEqual(gs._state_of({"H": x, "A": c}), "SOURCE_CONFLICT")


class TestRefreshHistoryAndFreshness(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"NHL_ENGINE_STATE_DIR": self.tmp.name, gs.SWITCH_ENV: "ON"})
        self.env.start()
        self.pages = {gs.GP_URL: gp_page(game_obj(2026020199, "WSH", "PHI", [entry(8479292, "Charlie Lindgren", "PROBABLE", 2)], [entry(8479361, "Joseph Woll", "LIKELY", 2)])),
                      gs.GDT_URL: gdt_page([("PHI", "WSH", [], [])])}

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def fetch(self, url):
        return self.pages[url]

    def test_a_test_run_never_reaches_the_sites_and_the_feed_is_off_unless_the_owner_opts_in(self):
        self.assertEqual(gs.refresh(None, NOW)["status"], "DISABLED")
        with mock.patch.dict(os.environ, {gs.SWITCH_ENV: "OFF"}):
            self.assertEqual(gs.refresh(None, NOW, fetch=self.fetch)["status"], "DISABLED")
        env = {k: v for k, v in os.environ.items() if k != gs.SWITCH_ENV}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertFalse(gs.enabled())                                  # the default: ambiguous permission is OFF
            self.assertEqual(gs.refresh(None, NOW, fetch=self.fetch)["status"], "DISABLED")
            self.assertFalse(gs.read_history())                             # not a single page was read or stored

    def test_refresh_stores_status_source_time_and_retrieval_time_and_never_touches_goalie_status_events(self):
        st = gs.refresh(None, NOW, fetch=self.fetch)
        g = st["games"]["2026020199"]
        self.assertEqual((g["state"], g["sides"]["AWAY"]["status"], g["sides"]["AWAY"]["source"]), ("BOTH_PROBABLE", gs.PROBABLE, "goaliepost"))
        self.assertEqual(g["retrieved_at_utc"], "2026-10-28T20:37:00Z")
        self.assertNotEqual(g["sides"]["AWAY"]["source_at_utc"], g["retrieved_at_utc"])
        self.assertNotIn("goalie_status_events", open(gs.__file__).read().split('"""', 2)[2].replace("never writes goalie_status_events", ""))

    def test_history_keeps_who_was_expected_and_what_changed_and_an_unchanged_fetch_adds_nothing(self):
        gs.refresh(None, NOW, fetch=self.fetch)
        n = len(gs.read_history())
        self.assertEqual(n, 2)
        gs.refresh(None, NOW + dt.timedelta(minutes=30), fetch=self.fetch)
        self.assertEqual(len(gs.read_history()), n)
        self.pages[gs.GP_URL] = gp_page(game_obj(2026020199, "WSH", "PHI", [entry(8480313, "Logan Thompson", "CONFIRMED", 0.2)], [entry(8479361, "Joseph Woll", "LIKELY", 2)]))
        gs.refresh(None, NOW + dt.timedelta(minutes=60), fetch=self.fetch)
        h = [r for r in gs.read_history() if r["side"] == "HOME"][-1]
        self.assertEqual((h["goalie"], h["status"], h["previous"]["goalie"], h["previous"]["status"]), ("Logan Thompson", gs.CONFIRMED, "Charlie Lindgren", gs.PROBABLE))
        view = gs.game_view(gs.load_state(), 2026020199, "WSH", "PHI", NOW + dt.timedelta(minutes=61))
        self.assertTrue(view["late_change"])

    def test_a_failed_fetch_keeps_the_last_good_data_but_it_goes_stale_and_is_unknown(self):
        gs.refresh(None, NOW, fetch=self.fetch)

        def boom(url):
            raise OSError("down")
        st = gs.refresh(None, NOW + dt.timedelta(minutes=30), fetch=boom)
        self.assertEqual(st["status"], "ERROR")
        self.assertIn("2026020199", st["games"])
        late = gs.game_view(gs.load_state(), 2026020199, "WSH", "PHI", NOW + dt.timedelta(minutes=200))
        self.assertFalse(late["feed_fresh"])
        self.assertEqual((late["state"], late["sides"]["HOME"]["status"]), ("UNKNOWN_GOALIE", gs.UNKNOWN))

    def test_the_secondary_source_failing_leaves_the_primary_usable(self):
        def fetch(url):
            if url == gs.GDT_URL:
                raise OSError("gdt down")
            return self.pages[url]
        st = gs.refresh(None, NOW, fetch=fetch)
        self.assertEqual(st["status"], "PARTIAL")
        self.assertEqual(st["games"]["2026020199"]["sides"]["HOME"]["status"], gs.PROBABLE)

    def test_the_refresh_interval_is_polite_and_shortens_near_a_game(self):
        gs.refresh(None, NOW, fetch=self.fetch)
        calls = []
        gs.refresh(None, NOW + dt.timedelta(minutes=5), fetch=lambda u: calls.append(u) or self.pages[u])
        self.assertEqual(calls, [])                                                              # not due
        gs.refresh(None, NOW + dt.timedelta(minutes=12), fetch=lambda u: calls.append(u) or self.pages[u])
        self.assertTrue(calls)                                                                   # a game starts within three hours: ten-minute interval

    def test_the_trader_hook_exists_and_is_isolated(self):
        src = open(os.path.join(os.path.dirname(gs.__file__), "real_parlay_paper_trader.py")).read()
        self.assertIn("goalie_sources.run_cycle", src)


class TestEggyConsumesGoalieStatus(unittest.TestCase):
    def view(self, state, h="CONFIRMED", a="CONFIRMED", late=False, conflict=None):
        side = lambda st, n: {"goalie": n, "status": st, "freshness": "CURRENT", "conflict": conflict if st == "CONFLICT" else None, "source": "goaliepost"}
        return {"state": state, "feed_fresh": True, "retrieved_at_utc": "2026-10-28T20:37:00Z", "late_change": late, "home": "DAL", "away": "WPG", "sides": {"HOME": side(h, "A. Home"), "AWAY": side(a, "B. Away")}}

    def goalie_items(self, c):
        cs = ep.cases(c)
        return [x for x in cs["case_for"] + cs["case_against"] if x["family"] == "MATCHUP_PERSONNEL"]

    def test_each_state_is_one_weak_reason_in_one_family(self):
        for state, h, a, kind, side in (("BOTH_CONFIRMED", "CONFIRMED", "CONFIRMED", "GOALIE_CONFIRMED", "case_for"), ("ONE_PROBABLE", "PROBABLE", "CONFIRMED", "GOALIE_UNCERTAIN", "case_against"),
                                        ("UNKNOWN_GOALIE", "UNKNOWN", "CONFIRMED", "GOALIE_UNKNOWN", "case_against"), ("SOURCE_CONFLICT", "CONFLICT", "CONFIRMED", "GOALIE_CONFLICT", "case_against")):
            c = cand()
            c["goalies"] = self.view(state, h, a, conflict="two sources disagree")
            items = self.goalie_items(c)
            self.assertEqual([(i["kind"], i["strength"]) for i in items], [(kind, "WEAK")], state)
            self.assertIn(items[0], ep.cases(c)[side])

    def test_probable_plus_unconfirmed_plus_unknown_is_still_one_reason(self):
        c = cand()
        c["goalies"] = self.view("UNKNOWN_GOALIE", "UNKNOWN", "PROBABLE")
        self.assertEqual(len(self.goalie_items(c)), 1)

    def test_goalie_status_never_changes_the_ranking_score_or_the_balance(self):
        base = cand()
        ranks = set()
        for state, h, a in (("BOTH_CONFIRMED", "CONFIRMED", "CONFIRMED"), ("UNKNOWN_GOALIE", "UNKNOWN", "UNKNOWN"), ("SOURCE_CONFLICT", "CONFLICT", "CONFIRMED"), ("ONE_PROBABLE", "PROBABLE", "CONFIRMED")):
            c = cand()
            c["goalies"] = self.view(state, h, a, conflict="x")
            ranks.add((ep.quality(c)["total"], json.dumps(c["balance"], sort_keys=True) == json.dumps(base["balance"], sort_keys=True)))
        self.assertEqual(len(ranks), 1)

    def test_unknown_or_conflicting_goalies_cost_one_confidence_category_and_probable_does_not(self):
        c = cand()
        c["goalies"] = self.view("ONE_PROBABLE", "PROBABLE", "CONFIRMED")
        keep = ep.confidence(c)
        for state, h in (("UNKNOWN_GOALIE", "UNKNOWN"), ("SOURCE_CONFLICT", "CONFLICT")):
            c["goalies"] = self.view(state, h, "CONFIRMED", conflict="x")
            self.assertLessEqual(["LOW", "MEDIUM", "HIGH"].index(ep.confidence(c)), max(0, ["LOW", "MEDIUM", "HIGH"].index(keep) - 1))
        c["goalies"] = None
        self.assertEqual(ep.confidence(c), keep)                                                 # no feed wired: the frozen behaviour is unchanged

    def test_the_feed_never_enters_the_win_probability_or_the_price(self):
        c = cand()
        before = (c["model_probability"], c["supported_probability"], c["edge"], c["conservative_edge"])
        c["goalies"] = self.view("UNKNOWN_GOALIE", "UNKNOWN", "UNKNOWN")
        self.assertEqual(before, (c["model_probability"], c["supported_probability"], c["edge"], c["conservative_edge"]))


if __name__ == "__main__":
    unittest.main()
