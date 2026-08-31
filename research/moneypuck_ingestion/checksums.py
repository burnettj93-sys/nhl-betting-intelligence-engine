"""
SHA-256 checksum helpers for the MoneyPuck research ingestion pipeline.

FIXES the checksum bug flagged in MONEYPUCK_DATA_CONTRACT_REVIEW.md
Section C: that review's in-browser SHA-256 helper had an off-by-one
hex-encoding bug that appended one extra character, producing 65-hex-char
strings instead of the correct 64 -- flagged there as unverified pending a
clean re-hash, "no other checksum in this report should be trusted
without regenerating it."

The fix here is to not hand-roll hex encoding at all: hashlib.sha256(...)
.hexdigest() always produces a correct, lowercase, exactly-64-character
hex string. Every checksum this module produces is validated against
is_valid_sha256_hex() before being returned, so a repeat of that bug
cannot silently ship a bad checksum again -- see
tests/test_moneypuck_ingestion.py's checksum regression tests.
"""
from __future__ import annotations

import hashlib
import re

_SHA256_HEX_RE = re.compile(r"^[0-9a-f]{64}$")


def is_valid_sha256_hex(digest: str) -> bool:
    """A SHA-256 hex digest must be exactly 64 lowercase hex characters."""
    return isinstance(digest, str) and bool(_SHA256_HEX_RE.fullmatch(digest))


class ChecksumError(RuntimeError):
    """Raised if a computed checksum ever fails is_valid_sha256_hex() --
    should be structurally impossible given hashlib.hexdigest(), but this
    guards against ever silently accepting a malformed checksum again."""


def sha256_hex_of_bytes(data: bytes) -> str:
    digest = hashlib.sha256(data).hexdigest()
    if not is_valid_sha256_hex(digest):
        raise ChecksumError(f"computed digest is not valid 64-char hex: {digest!r}")
    return digest


def sha256_hex_of_file(path: str, chunk_size: int = 8 * 1024 * 1024) -> str:
    """Streams the file in chunks (safe for the ~126MB MoneyPuck team
    file) rather than reading it fully into memory."""
    hasher = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            hasher.update(chunk)
    digest = hasher.hexdigest()
    if not is_valid_sha256_hex(digest):
        raise ChecksumError(f"computed digest is not valid 64-char hex: {digest!r}")
    return digest
