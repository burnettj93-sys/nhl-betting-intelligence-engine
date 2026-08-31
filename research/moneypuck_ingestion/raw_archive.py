"""
Raw-file archival for the MoneyPuck research ingestion pipeline --
implements the "Y. Raw snapshot architecture" design from
MONEYPUCK_DATA_CONTRACT_REVIEW.md: every downloaded source file is
archived UNCHANGED, alongside a small sidecar JSON record capturing
provenance. Raw files are never mutated after archival.

This module never fabricates downloaded_at_utc -- callers must supply the
real moment the file was actually obtained (e.g. the source file's own
mtime, if that's a trustworthy record of when a human downloaded it, or
the moment this pipeline itself performed a fetch). See
ingest_moneypuck_team.py's CLI for how this is wired end to end.
"""
from __future__ import annotations

import json
import os
import shutil
from dataclasses import asdict, dataclass

from research.moneypuck_ingestion.checksums import sha256_hex_of_file

RAW_ROOT = os.path.join(os.path.dirname(__file__), "raw")


@dataclass
class RawFileProvenance:
    original_filename: str
    source_url: str
    dataset: str
    downloaded_at_utc: str
    season_coverage: str
    byte_size: int
    sha256: str
    validation_status: str  # PENDING / VALID / REJECTED
    validation_reason: str | None = None


def archive_raw_file(local_path: str, *, source_url: str, dataset: str,
                      downloaded_at_utc: str, season_coverage: str = "all"
                      ) -> tuple[str, RawFileProvenance]:
    """Copies `local_path` (unchanged) into research/moneypuck_ingestion/raw/
    under a dataset/season-scoped directory, named with its download
    timestamp so a later re-download never overwrites an earlier archived
    snapshot, and writes a sidecar JSON provenance record next to it.

    Returns (archived_path, provenance). validation_status starts as
    PENDING -- the caller (ingest_moneypuck_team.py) sets it to VALID or
    REJECTED once schema validation has actually run, via
    mark_validation_status() below.
    """
    original_filename = os.path.basename(local_path)
    byte_size = os.path.getsize(local_path)
    sha256 = sha256_hex_of_file(local_path)

    safe_ts = downloaded_at_utc.replace("+00:00", "Z").replace(":", "")
    dest_dir = os.path.join(RAW_ROOT, dataset, season_coverage)
    os.makedirs(dest_dir, exist_ok=True)
    dest_name = f"{safe_ts}_{original_filename}"
    dest_path = os.path.join(dest_dir, dest_name)

    if os.path.exists(dest_path):
        existing_sha256 = sha256_hex_of_file(dest_path)
        if existing_sha256 != sha256:
            raise FileExistsError(
                f"{dest_path} already exists with a DIFFERENT checksum -- "
                f"refusing to overwrite an archived raw snapshot"
            )
    else:
        shutil.copyfile(local_path, dest_path)

    provenance = RawFileProvenance(
        original_filename=original_filename,
        source_url=source_url,
        dataset=dataset,
        downloaded_at_utc=downloaded_at_utc,
        season_coverage=season_coverage,
        byte_size=byte_size,
        sha256=sha256,
        validation_status="PENDING",
    )
    _write_sidecar(dest_path, provenance)
    return dest_path, provenance


def mark_validation_status(archived_path: str, status: str, reason: str | None = None) -> None:
    assert status in ("PENDING", "VALID", "REJECTED")
    provenance = read_sidecar(archived_path)
    provenance.validation_status = status
    provenance.validation_reason = reason
    _write_sidecar(archived_path, provenance)


def _sidecar_path(archived_path: str) -> str:
    return archived_path + ".provenance.json"


def _write_sidecar(archived_path: str, provenance: RawFileProvenance) -> None:
    with open(_sidecar_path(archived_path), "w") as f:
        json.dump(asdict(provenance), f, indent=2, sort_keys=True)


def read_sidecar(archived_path: str) -> RawFileProvenance:
    with open(_sidecar_path(archived_path)) as f:
        data = json.load(f)
    return RawFileProvenance(**data)
