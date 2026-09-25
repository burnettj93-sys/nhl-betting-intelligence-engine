"""
Where the engine keeps its mutable runtime STATE files (Live Run Reliability block, 2026-09-25).

Production: `operational/runtime/<name>` (gitignored) and the two append-only contract logs under
`operational/`. Tests: NEVER the production files. Found 2026-09-25: a test run had written `fixture-*`
records into the real prop candidate log, and other tests wrote real-shaped state (dates in the future,
fake event ids) into `operational/runtime/`. Every module routes its state files through `path()`; when
the process is a unit-test run (`python -m unittest`, pytest, or NHL_ENGINE_UNDER_TEST=1) and no explicit
NHL_ENGINE_STATE_DIR is given, all of them resolve into ONE throw-away temp directory that is removed at
exit. Tests that need specific paths still patch the module constants explicitly, which wins.

Override for operators/tests: NHL_ENGINE_STATE_DIR=/some/dir (used verbatim, never auto-removed).
"""
from __future__ import annotations

import atexit
import os
import shutil
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PRODUCTION_RUNTIME_DIR = REPO_ROOT / "operational" / "runtime"
PRODUCTION_OPERATIONAL_DIR = REPO_ROOT / "operational"
ENV_DIR = "NHL_ENGINE_STATE_DIR"
ENV_TESTING = "NHL_ENGINE_UNDER_TEST"

_test_dir: Path | None = None


def _detect() -> bool:
    if os.environ.get(ENV_TESTING, "").strip() in ("1", "true", "TRUE", "yes"):
        return True
    if "PYTEST_CURRENT_TEST" in os.environ:
        return True
    argv0 = ((sys.argv[0] if sys.argv else "") or "").replace("\\", "/")
    if "unittest" in argv0 or "pytest" in argv0 or argv0.endswith("/py.test"):
        return True                                   # `python -m unittest ...` reports argv[0] as "python -m unittest"
    spec = getattr(sys.modules.get("__main__"), "__spec__", None)
    return bool(spec and str(getattr(spec, "name", "")).split(".")[0] in ("unittest", "pytest"))


def under_test() -> bool:
    """True inside a unit-test run. The first detection also exports NHL_ENGINE_UNDER_TEST=1 so that every CHILD
    process a test spawns (found 2026-09-25: a test-launched morning catch-up ran real sync/settlement jobs whose
    children published to GitHub) is itself recognised as a test process and refuses real side effects."""
    if _detect():
        os.environ.setdefault(ENV_TESTING, "1")
        return True
    return False


def state_dir() -> Path | None:
    """The redirected state directory, or None when production paths apply."""
    global _test_dir
    explicit = os.environ.get(ENV_DIR, "").strip()
    if explicit:
        return Path(explicit)
    if under_test():
        if _test_dir is None:
            _test_dir = Path(tempfile.mkdtemp(prefix="nhl_engine_test_state_"))
            atexit.register(shutil.rmtree, str(_test_dir), True)
        return _test_dir
    return None


def path(name: str, *, area: str = "runtime") -> Path:
    """`area='runtime'` -> operational/runtime/<name>; `area='operational'` -> operational/<name>."""
    redirected = state_dir()
    if redirected is not None:
        return redirected / area / name
    base = PRODUCTION_RUNTIME_DIR if area == "runtime" else PRODUCTION_OPERATIONAL_DIR
    return base / name


def is_production_path(p: Path) -> bool:
    try:
        p = Path(p).resolve()
    except OSError:
        return False
    return PRODUCTION_RUNTIME_DIR.resolve() in p.parents or p.parent == PRODUCTION_OPERATIONAL_DIR.resolve()
