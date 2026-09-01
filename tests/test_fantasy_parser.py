"""
Tests for fantasy/yahoo/parser.py, against REAL sample XML fetched live
from Yahoo's own documentation this sprint (see fantasy/yahoo/contracts.py
for the source URLs) -- not invented fixtures (Part 137, only where
actually verified).
"""
from __future__ import annotations

import unittest

from fantasy.yahoo.parser import (
    extract_game, extract_league_metadata, extract_league_settings, extract_standings,
    parse_fantasy_content,
)

# Real sample XML, fetched from https://sports.yahoo.com/developer/docs/ this sprint.
REAL_GAME_XML = """<?xml version="1.0" encoding="UTF-8"?>
<fantasy_content xml:lang="en-US" yahoo:uri="https://fantasysports.yahooapis.com/fantasy/v2/game/nfl" xmlns:yahoo="http://www.yahooapis.com/v1/base.rng" time="30.575037002563ms" copyright="Data provided by Yahoo! and STATS, LLC" xmlns="https://fantasysports.yahooapis.com/fantasy/v2/base.rng">
<game>
<game_key>399</game_key>
<game_id>399</game_id>
<name>Football</name>
<code>nfl</code>
<type>full</type>
<url>https://football.fantasysports.yahoo.com/f1</url>
<season>2020</season>
<is_registration_over>0</is_registration_over>
<is_game_over>0</is_game_over>
<is_offseason>0</is_offseason>
</game>
</fantasy_content>"""

REAL_LEAGUE_SETTINGS_XML = """<?xml version="1.0" encoding="UTF-8"?>
<fantasy_content xml:lang="en-US" yahoo:uri="https://fantasyspots.yahooapis.com/fantasy/v2/league/390.l.1000" time="19.485950469971ms" copyright="Data provided by Yahoo! and STATS, LLC" refresh_rate="60" xmlns:yahoo="http://www.yahooapis.com/v1/base.rng" xmlns="http://fantasysports.yahooapis.com/fantasy/v2/base.rng">
<league>
<league_key>390.l.1000</league_key>
<league_id>1000</league_id>
<name>Yahoo Public 1000</name>
<draft_status>postdraft</draft_status>
<num_teams>10</num_teams>
<scoring_type>head</scoring_type>
<current_week>16</current_week>
<season>2019</season>
<settings>
<draft_type>live</draft_type>
<scoring_type>head</scoring_type>
<waiver_type>R</waiver_type>
<uses_faab>0</uses_faab>
<max_teams>10</max_teams>
<roster_positions>
<roster_position>
<position>QB</position>
<position_type>O</position_type>
<count>1</count>
</roster_position>
<roster_position>
<position>BN</position>
<count>6</count>
</roster_position>
</roster_positions>
<stat_categories>
<stats>
<stat>
<stat_id>4</stat_id>
<enabled>1</enabled>
<name>Passing Yards</name>
<display_name>Pass Yds</display_name>
<position_type>O</position_type>
</stat>
<stat>
<stat_id>5</stat_id>
<enabled>1</enabled>
<name>Passing Touchdowns</name>
<display_name>Pass TD</display_name>
<position_type>O</position_type>
</stat>
</stats>
</stat_categories>
<stat_modifiers>
<stats>
<stat>
<stat_id>4</stat_id>
<value>0.04</value>
</stat>
<stat>
<stat_id>5</stat_id>
<value>4</value>
</stat>
</stats>
</stat_modifiers>
<uses_fractional_points>1</uses_fractional_points>
</settings>
</league>
</fantasy_content>"""

REAL_STANDINGS_XML = """<?xml version="1.0" encoding="UTF-8"?>
<fantasy_content xml:lang="en-US" yahoo:uri="https://fantasyspots.yahooapis.com/fantasy/v2/league/390.l.1000/standings" time="143.89801025391ms" copyright="Data provided by Yahoo! and STATS, LLC" refresh_rate="60" xmlns:yahoo="http://www.yahooapis.com/v1/base.rng" xmlns="http://fantasysports.yahooapis.com/fantasy/v2/base.rng">
<league>
<league_key>390.l.1000</league_key>
<name>Yahoo Public 1000</name>
<standings>
<teams count="2">
<team>
<team_key>390.l.1000.t.10</team_key>
<team_id>10</team_id>
<name>Pierre's Team</name>
<team_standings>
<rank>1</rank>
<outcome_totals>
<wins>8</wins>
<losses>6</losses>
<ties>0</ties>
<percentage>.571</percentage>
</outcome_totals>
<points_for>1583.16</points_for>
<points_against>1447.26</points_against>
</team_standings>
</team>
<team>
<team_key>390.l.1000.t.7</team_key>
<team_id>7</team_id>
<name>ray's Team</name>
<team_standings>
<rank>2</rank>
<outcome_totals>
<wins>9</wins>
<losses>5</losses>
<ties>0</ties>
<percentage>.643</percentage>
</outcome_totals>
<points_for>1594.88</points_for>
<points_against>1454.78</points_against>
</team_standings>
</team>
</teams>
</standings>
</league>
</fantasy_content>"""


class TestParseFantasyContent(unittest.TestCase):
    def test_malformed_xml_raises_value_error_not_uncaught_exception(self):
        with self.assertRaises(ValueError):
            parse_fantasy_content("<not><valid</xml")

    def test_empty_but_well_formed_xml_returns_empty_dict_not_none(self):
        # Regression guard: the top-level result must always be a dict
        # (never None), even for a genuinely empty <fantasy_content/> --
        # every extract_* helper calls .get() on this return value and
        # would crash on None (a real bug found and fixed this sprint).
        result = parse_fantasy_content("<fantasy_content></fantasy_content>")
        self.assertEqual(result, {})

    def test_extract_helpers_never_crash_on_an_empty_response(self):
        parsed = parse_fantasy_content("<fantasy_content></fantasy_content>")
        self.assertEqual(extract_game(parsed), {})
        self.assertEqual(extract_league_metadata(parsed), {})
        self.assertEqual(extract_standings(parsed), [])


class TestExtractGame(unittest.TestCase):
    def test_extracts_real_verified_game_fields(self):
        parsed = parse_fantasy_content(REAL_GAME_XML)
        game = extract_game(parsed)
        self.assertEqual(game["game_key"], "399")
        self.assertEqual(game["code"], "nfl")
        self.assertEqual(game["season"], "2020")
        self.assertEqual(game["is_offseason"], "0")


class TestExtractLeagueSettings(unittest.TestCase):
    def test_extracts_real_verified_league_metadata(self):
        parsed = parse_fantasy_content(REAL_LEAGUE_SETTINGS_XML)
        metadata = extract_league_metadata(parsed)
        self.assertEqual(metadata["league_key"], "390.l.1000")
        self.assertEqual(metadata["draft_status"], "postdraft")
        self.assertEqual(metadata["num_teams"], "10")

    def test_extracts_real_verified_roster_positions(self):
        parsed = parse_fantasy_content(REAL_LEAGUE_SETTINGS_XML)
        settings = extract_league_settings(parsed)
        positions = {rp["position"]: rp["count"] for rp in settings["roster_positions"]}
        self.assertEqual(positions["QB"], "1")
        self.assertEqual(positions["BN"], "6")

    def test_extracts_real_verified_stat_categories(self):
        parsed = parse_fantasy_content(REAL_LEAGUE_SETTINGS_XML)
        settings = extract_league_settings(parsed)
        names = {s["stat_id"]: s["name"] for s in settings["stat_categories"]}
        self.assertEqual(names["4"], "Passing Yards")
        self.assertEqual(names["5"], "Passing Touchdowns")

    def test_extracts_real_verified_stat_modifiers(self):
        parsed = parse_fantasy_content(REAL_LEAGUE_SETTINGS_XML)
        settings = extract_league_settings(parsed)
        values = {m["stat_id"]: m["value"] for m in settings["stat_modifiers"]}
        self.assertEqual(values["4"], "0.04")
        self.assertEqual(values["5"], "4")

    def test_uses_fractional_points_flag_preserved(self):
        parsed = parse_fantasy_content(REAL_LEAGUE_SETTINGS_XML)
        settings = extract_league_settings(parsed)
        self.assertEqual(settings["uses_fractional_points"], "1")


class TestExtractStandings(unittest.TestCase):
    def test_extracts_real_verified_team_standings(self):
        parsed = parse_fantasy_content(REAL_STANDINGS_XML)
        teams = extract_standings(parsed)
        self.assertEqual(len(teams), 2)
        ranks = {t["name"]: t["team_standings"]["rank"] for t in teams}
        self.assertEqual(ranks["Pierre's Team"], "1")
        self.assertEqual(ranks["ray's Team"], "2")

    def test_outcome_totals_preserved(self):
        parsed = parse_fantasy_content(REAL_STANDINGS_XML)
        teams = extract_standings(parsed)
        first = teams[0]["team_standings"]["outcome_totals"]
        self.assertEqual(first["wins"], "8")
        self.assertEqual(first["losses"], "6")


if __name__ == "__main__":
    unittest.main()
