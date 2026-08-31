"""
Raw-file archival for the real NHL play-by-play ingestion pilot (Part 17).

Mirrors research/moneypuck_ingestion/raw_archive.py's design (sha256
collision guard, sidecar provenance JSON, never mutates an archived file)
but roots at research/real_nhl_pbp/raw/ instead of the MoneyPuck package's
own raw/ tree -- play-by-play is a different provider/source and does not
belong inside the MoneyPuck-specific archive, even though the archival
*pattern* is intentionally identical. The checksum primitive itself is
imported, not re-implemented, so there is exactly one sha256-of-file
routine in the project.

This module never fabricates retrieved_at_utc -- callers must supply the
real moment the fetch actually happened.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass

from research.moneypuck_ingestion.checksums import sha256_hex_of_file

RAW_ROOT = os.path.join(os.path.dirname(__file__), "raw")


@dataclass
class PbpRawProvenance:
    game_id: int
    season: str
    source_url: str
    provider: str
    retrieved_at_utc: str
    byte_size: int
    sha256: str
    archival_status: str  # ARCHIVAL_RESEARCH (never LIVE_OBSERVED for historical backfill)


def archive_raw_pbp(local_path: str, *, game_id: int, season: str, source_url: str,
                     retrieved_at_utc: str, provider: str = "api-web.nhle.com"
                     ) -> tuple[str, PbpRawProvenance]:
    """Copies the raw play-by-play JSON response unchanged into
    research/real_nhl_pbp/raw/<season>/<game_id>.json and writes a sidecar
    provenance record. If a payload for this game_id is already archived,
    a byte-identical re-fetch is a no-op (idempotent, Part 24); a
    DIFFERENT payload for an already-archived game_id raises rather than
    silently overwriting history (revision provenance is preserved by
    refusing to clobber, matching the MoneyPuck archive's own policy)."""
    byte_size = os.path.getsize(local_path)
    sha256 = sha256_hex_of_file(local_path)

    dest_dir = os.path.join(RAW_ROOT, str(season))
    os.makedirs(dest_dir, exist_ok=True)
    dest_path = os.path.join(dest_dir, f"{game_id}.json")

    if os.path.exists(dest_path):
        existing_sha256 = sha256_hex_of_file(dest_path)
        if existing_sha256 != sha256:
            raise FileExistsError(
                f"{dest_path} already archived with a DIFFERENT checksum -- "
                f"refusing to overwrite archived play-by-play history for game {game_id}"
            )
        # Part 24 idempotency, taken literally: a byte-identical re-fetch is
        # UNCHANGED -- that means the ORIGINAL provenance record (including
        # its original retrieved_at_utc/source_url) is preserved exactly,
        # not silently replaced with this call's arguments. Returning early
        # here is what makes a duplicate archival attempt a true no-op
        # rather than a metadata-only overwrite.
        return dest_path, read_sidecar(dest_path)

    with open(local_path, "rb") as src, open(dest_path, "wb") as dst:
        dst.write(src.read())

    provenance = PbpRawProvenance(
        game_id=game_id,
        season=str(season),
        source_url=source_url,
        provider=provider,
        retrieved_at_utc=retrieved_at_utc,
        byte_size=byte_size,
        sha256=sha256,
        archival_status="ARCHIVAL_RESEARCH",
    )
    _write_sidecar(dest_path, provenance)
    return dest_path, provenance


def _sidecar_path(archived_path: str) -> str:
    return archived_path + ".provenance.json"


def _write_sidecar(archived_path: str, provenance: PbpRawProvenance) -> None:
    with open(_sidecar_path(archived_path), "w") as f:
        json.dump(asdict(provenance), f, indent=2, sort_keys=True)


def read_sidecar(archived_path: str) -> PbpRawProvenance:
    with open(_sidecar_path(archived_path)) as f:
        data = json.load(f)
    return PbpRawProvenance(**data)


def archived_game_ids(season: str) -> list[int]:
    """Real game_ids currently archived for a season (sorted), by scanning
    the raw/<season>/ directory -- never inferred or assumed."""
    season_dir = os.path.join(RAW_ROOT, str(season))
    if not os.path.isdir(season_dir):
        return []
    ids = []
    for name in os.listdir(season_dir):
        if name.endswith(".json") and not name.endswith(".provenance.json"):
            ids.append(int(name[:-len(".json")]))
    return sorted(ids)


def load_raw_pbp(season: str, game_id: int) -> dict:
    path = os.path.join(RAW_ROOT, str(season), f"{game_id}.json")
    with open(path) as f:
        return json.load(f)
