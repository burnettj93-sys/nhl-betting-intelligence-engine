"""
Process diagnostics for the ADMIN-only Diagnostics page (Community Cloud
memory sprint, 2026-09-25, Part 23). Deliberately tiny and dependency-free
(no psutil): process RSS, Python/Streamlit versions, runtime mode, and which
heavy libraries/modules are currently loaded.

Never exposes environment variables, filesystem secrets, tokens or paths --
only the fields returned below.
"""
from __future__ import annotations

import platform
import resource
import subprocess
import sys

# Top-level module prefixes whose presence in sys.modules is informative for
# memory work. "research" is counted by module, the libraries are booleans.
_LIBRARIES = ("pandas", "numpy", "altair", "pyarrow", "requests", "cryptography")


def current_rss_mb() -> float | None:
    """Resident set size of THIS process in MB, or None if unavailable."""
    try:  # Linux (Community Cloud): /proc is exact and free
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return round(int(line.split()[1]) / 1024, 1)
    except OSError:
        pass
    try:  # macOS / other POSIX
        import os
        out = subprocess.run(["ps", "-o", "rss=", "-p", str(os.getpid())],
                             capture_output=True, text=True, timeout=3).stdout.strip()
        return round(int(out) / 1024, 1)
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def peak_rss_mb() -> float:
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # ru_maxrss is bytes on macOS, kilobytes on Linux.
    return round(peak / (1024 * 1024) if sys.platform == "darwin" else peak / 1024, 1)


def _snapshot_diagnostics() -> dict:
    """Snapshot source / remote fetch status / last success / generated_at / age / schema /
    content hash (Cloud live-data sprint, Part 24). Never includes a token or a URL query."""
    from dashboard import snapshot_source
    try:
        return snapshot_source.diagnostics()
    except Exception as exc:  # noqa: BLE001 -- diagnostics must never break the page
        return {"snapshot_source": "ERROR", "last_error": f"{type(exc).__name__}"}


def process_diagnostics() -> dict:
    import streamlit as st
    from operational import runtime_mode
    loaded = set(sys.modules)
    return {
        "runtime_mode": runtime_mode.current_mode(),
        "rss_mb": current_rss_mb(),
        "peak_rss_mb": peak_rss_mb(),
        "python_version": platform.python_version(),
        "streamlit_version": st.__version__,
        "libraries_loaded": {name: name in loaded for name in _LIBRARIES},
        "research_modules_loaded": sum(1 for m in loaded if m == "research" or m.startswith("research.")),
        "model_stack_loaded": "research.context_overlay.prediction_stack" in loaded,
        "snapshot": _snapshot_diagnostics(),
    }
