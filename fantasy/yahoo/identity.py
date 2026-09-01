"""
Yahoo <-> NHL player identity reconciliation (Part 16/17).

Deliberately self-contained (not imported from
research/live_sog_pricing/player_mapping.py, which solves a similar
problem for the betting engine) -- see this package's ABSOLUTE
SEPARATION rule in fantasy/__init__.py. A small amount of logic
duplication here is the price of keeping fantasy/ fully isolated from
the betting pricing pipeline, not a shortcut.

FAIL CLOSED (Part 17): never silently maps an uncertain player. Returns
one of MATCHED / AMBIGUOUS / UNMATCHED for every lookup; callers must
downgrade any fantasy projection that depends on an AMBIGUOUS or
UNMATCHED identity rather than presenting it with the same confidence
as a MATCHED one.
"""
from __future__ import annotations

import unicodedata
from dataclasses import dataclass

_SUFFIXES = (" jr.", " jr", " sr.", " sr", " ii", " iii", " iv")


def normalize_name(name: str) -> str:
    """Lowercase, strip accents, strip common suffixes, collapse
    whitespace, drop punctuation. This is intentionally the ONLY string
    transform used for matching -- no fuzzy/edit-distance matching
    anywhere in this module (Part 17: fail closed, never a loose
    string-similarity guess)."""
    normalized = unicodedata.normalize("NFKD", name)
    ascii_name = "".join(c for c in normalized if not unicodedata.combining(c))
    ascii_name = ascii_name.lower().strip()
    for suffix in _SUFFIXES:
        if ascii_name.endswith(suffix):
            ascii_name = ascii_name[: -len(suffix)].strip()
    ascii_name = "".join(c for c in ascii_name if c.isalnum() or c.isspace())
    return " ".join(ascii_name.split())


@dataclass(frozen=True)
class IdentityMatch:
    status: str  # "MATCHED" | "AMBIGUOUS" | "UNMATCHED"
    player_id: str | None
    candidates: tuple[str, ...] = ()  # player_ids, populated only when AMBIGUOUS
    reason: str = ""


class PlayerIdentityIndex:
    """Built once from a real, already-known set of (player_id, name,
    team) tuples -- e.g. this engine's own real player corpus, NOT
    fabricated. Never matches on last name alone (Part 17's own
    explicit rule, mirrored from the betting engine's equivalent
    guard)."""

    def __init__(self, players: list[tuple[str, str, str]]):
        """`players`: list of (player_id, full_name, team_abbr)."""
        self._by_norm_name: dict[str, list[tuple[str, str]]] = {}
        for player_id, name, team in players:
            key = normalize_name(name)
            self._by_norm_name.setdefault(key, []).append((player_id, team))

    def match(self, yahoo_name: str, yahoo_team: str | None = None) -> IdentityMatch:
        key = normalize_name(yahoo_name)
        candidates = self._by_norm_name.get(key, [])
        if not candidates:
            return IdentityMatch(status="UNMATCHED", player_id=None,
                                  reason=f"no NHL player found matching normalized name {key!r}")
        if len(candidates) == 1:
            return IdentityMatch(status="MATCHED", player_id=candidates[0][0])

        # Multiple real players share this normalized name -- only a
        # matching team disambiguates; anything else stays AMBIGUOUS
        # rather than guessing (Part 17).
        if yahoo_team:
            team_matches = [pid for pid, team in candidates if team.upper() == yahoo_team.upper()]
            if len(team_matches) == 1:
                return IdentityMatch(status="MATCHED", player_id=team_matches[0])

        return IdentityMatch(status="AMBIGUOUS", player_id=None,
                              candidates=tuple(pid for pid, _team in candidates),
                              reason=f"{len(candidates)} real NHL players share the normalized name {key!r}; "
                                     f"team {yahoo_team!r} did not uniquely disambiguate")
