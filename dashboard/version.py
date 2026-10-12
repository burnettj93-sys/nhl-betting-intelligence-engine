"""Which code and which data the page in front of you is. Public, read-only, never raises.

`code_commit()` is the commit the hosted app is running (read from the clone's .git), `data_commit()` the engine commit that built the published data (recorded in the snapshot's metadata).
They normally match within one deploy; when they differ the footer says so, so a stale deploy or a stale publication cannot hide."""
from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def code_commit(repo: Path | None = None) -> str | None:
    repo = repo or REPO_ROOT
    try:
        head = (repo / ".git" / "HEAD").read_text().strip()
        if head.startswith("ref:"):
            ref = head.split(" ", 1)[1].strip()
            p = repo / ".git" / ref
            if p.exists():
                return p.read_text().strip()[:12]
            packed = repo / ".git" / "packed-refs"
            if packed.exists():
                for line in packed.read_text().splitlines():
                    if line.endswith(" " + ref) and not line.startswith("#"):
                        return line.split(" ", 1)[0][:12]
            return None
        return head[:12] or None
    except OSError:
        return None


def data_commit(snapshot: dict | None) -> str | None:
    try:
        return ((snapshot or {}).get("metadata") or {}).get("source_master_commit")
    except AttributeError:
        return None


def footer_text(code: str | None, data: str | None) -> str:
    if not code and not data:
        return ""
    if code and data and not (code.startswith(data[:7]) or data.startswith(code[:7])):
        return f"Code {code[:7]} · data built by {data[:7]} — different versions: the app or the published data is one deploy behind."
    return f"Code {(code or data or '')[:7]} · data built by {(data or code or '')[:7]}"
