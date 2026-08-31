"""
v2.1.1 spec item 5: tests/test_structural_reads.py audits direct SQL
reads of the bitemporal tables, but does not catch a *higher-level*
anti-pattern -- production code using a game's `game_id`, or its
position in a list, as a stand-in for "was this game's result known
yet." That was exactly the bug closed in run_slate.py by item 1:
`[g for g in all_final if g < gid]` and
`train_ids, test_ids = all_final[:-5], all_final[-5:]`. `game_id` is an
arbitrary identifier with no guaranteed relationship to real-world
chronology -- a rescheduled or late-finishing game can easily have a
LOWER numeric id than a game that both started and finished earlier --
so using it (or list position) to gate what a model is allowed to
learn is a temporal-integrity bug even though it never touches a
restricted table directly and so is invisible to test_structural_reads.

This audit walks the AST (not a text/regex scan) of every production
.py file, so docstrings, comments, and English prose mentioning these
exact patterns -- e.g. this file's own docstring, or run_slate.py's
module docstring explaining what NOT to do -- can never produce a
false positive; only real code constructs are inspected. It flags:

  1. A comparison (<, >, <=, >=) where either side is a bare name/
     attribute that looks like a game identifier (game_id, gid,
     target_game_id, and similar).
  2. A slice subscript (`x[...]`) where the base being sliced looks
     like a collection of games (all_final, game_ids, games, held_out,
     train_ids/test_ids, and similar).
  3. A call to `sorted(...)` where an argument expression mentions a
     name that looks like a game id or a collection of them.

The test does not need to (and does not try to) prohibit every use of
a game_id in the codebase -- it must prevent game_id from becoming a
proxy for historical information availability. See JUSTIFIED_EXCEPTIONS
below for this codebase's one legitimate, narrowly-scoped, commented
exception (run_slate.py's purely-cosmetic "which 5 games to print"
selection); anything else must either be rewritten to route eligibility
through features/point_in_time.py's PIT functions (never game_id/list
position), or individually justified here with a comment explaining
why it's safe.
"""
import ast
import pathlib
import unittest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent

# test scaffolding builds its own precise temporal scenarios (fixed
# game_ids in fixed, explicitly-commented chronological order) purely
# to exercise/assert against production behavior -- it is not itself
# the production training-eligibility path this audit guards.
EXEMPT_DIR_PREFIXES = ("tests/",)

_GAME_ID_NAME_HINTS = {"gid", "game_id", "target_gid", "target_game_id"}
_GAME_COLLECTION_NAME_HINTS = {
    "games", "game_ids", "all_final", "held_out", "test_ids", "train_ids",
    "game_list", "demo_display_ids",
}


def _name_or_attr_str(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _looks_like_game_id(node):
    s = _name_or_attr_str(node)
    if s is None:
        return False
    s = s.lower()
    return (s in _GAME_ID_NAME_HINTS or s.endswith("_gid")
            or s.endswith("_game_id"))


def _looks_like_game_collection(node):
    s = _name_or_attr_str(node)
    if s is None:
        return False
    s = s.lower()
    if s in _GAME_COLLECTION_NAME_HINTS:
        return True
    return "game" in s and ("ids" in s or s.endswith("games") or "final" in s)


def _mentions_game_id_like(node):
    for n in ast.walk(node):
        if isinstance(n, (ast.Name, ast.Attribute)):
            if _looks_like_game_id(n) or _looks_like_game_collection(n):
                return True
    return False


def _snippet(source_lines, lineno):
    if 1 <= lineno <= len(source_lines):
        return source_lines[lineno - 1].strip()
    return "<unavailable>"


def _find_violations(tree, source_lines):
    """Returns a list of (lineno, kind, snippet) for every suspicious
    game-id/list-position construct found in an already-parsed AST."""
    violations = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare):
            operands = [node.left] + list(node.comparators)
            for i, op in enumerate(node.ops):
                if isinstance(op, (ast.Lt, ast.Gt, ast.LtE, ast.GtE)):
                    left, right = operands[i], operands[i + 1]
                    if _looks_like_game_id(left) or _looks_like_game_id(right):
                        violations.append(
                            (node.lineno, "game_id_comparison", _snippet(source_lines, node.lineno)))
        elif isinstance(node, ast.Subscript) and isinstance(getattr(node, "slice", None), ast.Slice):
            if _looks_like_game_collection(node.value):
                violations.append(
                    (node.lineno, "game_list_slice", _snippet(source_lines, node.lineno)))
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "sorted":
            key_kwarg = next((kw for kw in node.keywords if kw.arg == "key"), None)
            if key_kwarg is not None:
                # an explicit key= determines sort order -- this is only a
                # training-eligibility proxy if the SORT KEY ITSELF derives
                # from a game id (e.g. key=lambda g: g.game_id). A key that
                # sorts by something else entirely (e.g. a timestamp) is
                # not a violation merely because a game id also happens to
                # ride along elsewhere in the sorted data.
                if _mentions_game_id_like(key_kwarg.value):
                    violations.append(
                        (node.lineno, "sorted_by_game_id", _snippet(source_lines, node.lineno)))
            elif node.args and _mentions_game_id_like(node.args[0]):
                # no key -- natural/default ordering is used, so flag when
                # the collection being sorted itself looks like game ids
                # (e.g. sorted(game_ids)).
                violations.append(
                    (node.lineno, "sorted_by_game_id", _snippet(source_lines, node.lineno)))
    return violations


def _iter_production_py_files():
    for py_path in sorted(REPO_ROOT.rglob("*.py")):
        rel = py_path.relative_to(REPO_ROOT).as_posix()
        if any(rel.startswith(p) for p in EXEMPT_DIR_PREFIXES):
            continue
        yield rel, py_path


def _violations_for_file(rel, py_path):
    text = py_path.read_text()
    tree = ast.parse(text, filename=rel)
    return _find_violations(tree, text.splitlines())


# (file, kind, marker substring of the flagged line, justification).
# Every entry here must still genuinely exist in source (see
# test_every_justified_exception_still_exists_in_source below) and must
# never appear anywhere on the actual training/prediction path -- see
# test_no_exception_is_granted_to_a_predictive_or_backtest_module.
JUSTIFIED_EXCEPTIONS = [
    {
        "file": "run_slate.py",
        "kind": "game_list_slice",
        "marker": "demo_display_ids = all_final[-5:]",
        "reason": (
            "purely cosmetic selection of which games to print in the demo "
            "script's output. It never gates what any model instance is "
            "allowed to learn: build_prediction_for_game() (called once per "
            "game, including every one of these) independently reconstructs "
            "model state via build_model_state_as_of(), keyed strictly to "
            "that game's own prediction_time_utc -- see "
            "tests/test_run_slate_temporal.py."
        ),
    },
    {
        "file": "research/run_special_teams_role_transitions.py",
        "kind": "game_list_slice",
        "marker": "recent_slice = games[max(0, i - recent_n):i]",
        "reason": (
            "False-positive trigger: `games` here is one PLAYER's own real games sorted "
            "chronologically, and the slice builds a PIT-safe 'strictly before target game i' "
            "window for a special-teams ROLE-TRANSITION FEATURE classifier "
            "(research/period_event_timing/special_teams_roles.py::classify_role_state) -- not a "
            "model training-eligibility split. The slice upper bound is `i` (exclusive), so game i "
            "itself is never included; this is the intended PIT guarantee, not a leak. This code "
            "path never touches a model, a fit, or a training/eval split."
        ),
    },
    {
        "file": "research/run_special_teams_role_transitions.py",
        "kind": "game_list_slice",
        "marker": "baseline_slice = games[max(0, i - recent_n - baseline_n):max(0, i - recent_n)]",
        "reason": (
            "Same false positive as the recent_slice entry immediately above, for the paired "
            "'baseline window' slice (the games before the recent window, also strictly before "
            "target game i) -- see that entry's reason."
        ),
    },
    {
        "file": "operational/special_teams_roles_live.py",
        "kind": "game_list_slice",
        "marker": "recent_slice = current_team_games[-RECENT_GAMES:]",
        "reason": (
            "Same false positive as the run_special_teams_role_transitions.py entries above, "
            "for the LIVE/prospective version of the identical role-transition feature "
            "computation: `current_team_games` is one player's own real games for his current "
            "team, already filtered to strictly-before `as_of_date` by "
            "special_teams_history_store.player_history_before()'s own `game_date < before_date` "
            "query before this function ever sees the list. Slicing the last N games of an "
            "already-PIT-filtered list is the intended recent-window construction, not a "
            "training-eligibility split -- this code path never touches a model fit or a "
            "training/eval split, only the SOG shadow overlay's role-state feature."
        ),
    },
    {
        "file": "operational/special_teams_roles_live.py",
        "kind": "game_list_slice",
        "marker": "baseline_slice = current_team_games[-(RECENT_GAMES + BASELINE_GAMES):-RECENT_GAMES]",
        "reason": (
            "Same false positive as the recent_slice entry immediately above, for the paired "
            "'baseline window' slice (the games before the recent window, also already strictly "
            "before as_of_date) -- see that entry's reason."
        ),
    },
    {
        "file": "operational/live_odds_daily_pull.py",
        "kind": "sorted_by_game_id",
        "marker": "preseason_dates = sorted({",
        "reason": (
            "False-positive trigger: the detector flags this only because "
            "the comprehension iterates `for g in games` (the loop variable "
            "name `games` matches the game-collection heuristic) -- the "
            "actual values being sorted are `dt.date` objects derived from "
            "`gameDate`, not game ids or list positions, and this has "
            "nothing to do with any model's training/prediction eligibility. "
            "It is a live NHL schedule odds-pulling job's own lookup of the "
            "earliest real preseason calendar date (gameType == 1), used "
            "only to gate when this operational script starts making API "
            "calls -- it never touches a model, a feature, or a prediction."
        ),
    },
]


def _is_justified(rel, kind, snippet):
    return any(exc["file"] == rel and exc["kind"] == kind and exc["marker"] in snippet
               for exc in JUSTIFIED_EXCEPTIONS)


class TestNoGameIdOrListPositionTrainingProxies(unittest.TestCase):
    def test_no_unjustified_game_id_training_proxy_in_production_code(self):
        violations = []
        for rel, py_path in _iter_production_py_files():
            for lineno, kind, snippet in _violations_for_file(rel, py_path):
                if not _is_justified(rel, kind, snippet):
                    violations.append((rel, lineno, kind, snippet))
        self.assertEqual(
            violations, [],
            f"game_id or list-position used as an apparent training-eligibility "
            f"proxy in production code: {violations}. Route historical "
            f"eligibility through features/point_in_time.py's "
            f"completed_games_known_before()/build_model_state_as_of() instead, "
            f"or add a specifically-justified entry to JUSTIFIED_EXCEPTIONS in "
            f"this test with a comment explaining why it's safe (it must never "
            f"gate what any model is allowed to learn).")

    def test_every_justified_exception_still_exists_in_source(self):
        for exc in JUSTIFIED_EXCEPTIONS:
            py_path = REPO_ROOT / exc["file"]
            self.assertTrue(py_path.exists(), f"{exc['file']} no longer exists")
            found = _violations_for_file(exc["file"], py_path)
            self.assertTrue(
                any(kind == exc["kind"] and exc["marker"] in snippet for _, kind, snippet in found),
                f"stale JUSTIFIED_EXCEPTIONS entry: {exc['file']} no longer "
                f"contains {exc['marker']!r} -- remove this entry")

    def test_no_exception_is_granted_to_a_predictive_or_backtest_module(self):
        # belt-and-suspenders: the exception list itself must never cover a
        # module that actually decides what a model learns or predicts.
        forbidden_modules = {"models/combined_model.py", "pricing/engine.py",
                              "pricing/decision.py", "backtest.py",
                              "features/feature_engine.py", "features/point_in_time.py"}
        exempted_files = {exc["file"] for exc in JUSTIFIED_EXCEPTIONS}
        self.assertEqual(exempted_files & forbidden_modules, set())


class TestDetectorCatchesTheExactSpecExamples(unittest.TestCase):
    """Regression guard for the detector itself, using the exact
    anti-pattern snippets named in the v2.1.1 spec (item 1's flagged
    code) as synthetic source -- proves this audit would have caught
    the original bug, not just that it's silent on the current
    (already-fixed) codebase."""

    @staticmethod
    def _violations_in_source(source):
        tree = ast.parse(source)
        return _find_violations(tree, source.splitlines())

    def test_catches_game_id_less_than_comparison(self):
        violations = self._violations_in_source(
            "train = [g for g in all_final if g < gid]\n")
        self.assertTrue(any(k == "game_id_comparison" for _, k, _ in violations))

    def test_catches_list_position_training_split(self):
        violations = self._violations_in_source(
            "train_ids, test_ids = all_final[:-5], all_final[-5:]\n")
        kinds = [k for _, k, _ in violations]
        self.assertIn("game_list_slice", kinds)

    def test_catches_sorted_by_game_id(self):
        violations = self._violations_in_source("eligible = sorted(game_ids)\n")
        self.assertTrue(any(k == "sorted_by_game_id" for _, k, _ in violations))

    def test_does_not_flag_an_unrelated_comparison_or_slice(self):
        violations = self._violations_in_source(
            "x = [1, 2, 3][:2]\nif score < threshold:\n    pass\n")
        self.assertEqual(violations, [])

    def test_does_not_flag_a_docstring_or_comment_merely_mentioning_the_pattern(self):
        source = (
            '"""This module must never do g < gid or all_final[:-5] -- see '
            'features/point_in_time.py instead."""\n'
            "# also never sorted(game_ids) here\n"
            "x = 1\n"
        )
        violations = self._violations_in_source(source)
        self.assertEqual(violations, [])


class TestRestrictedSetsStayNarrowlyTargeted(unittest.TestCase):
    def test_ordinary_identifiers_are_not_treated_as_game_ids(self):
        for benign in ("score", "threshold", "id", "index", "team_id", "player_id"):
            node = ast.parse(benign, mode="eval").body
            self.assertFalse(_looks_like_game_id(node), benign)

    def test_ordinary_identifiers_are_not_treated_as_game_collections(self):
        for benign in ("scores", "players", "teams", "results", "items"):
            node = ast.parse(benign, mode="eval").body
            self.assertFalse(_looks_like_game_collection(node), benign)


if __name__ == "__main__":
    unittest.main()
