"""
Real Product Bridge block (2026-09-29): the single operational-layer entry
point that opens the real nhl.db connection and calls
dashboard/real_today_view.py::build_real_today_state() with it.

This exists as its own module -- separate from
operational/cloud_snapshot_builder.py -- specifically so
dashboard/pages/21_Today.py's LOCAL-mode branch can import it without ever
referencing "cloud_snapshot_builder" in a dashboard/*.py file at all: Cloud
mode always reads through dashboard/cloud_snapshot.py's own remote-fetch
wrapper, never the builder directly (tests/test_cloud_live_data.py::
test_cloud_pages_never_import_the_publisher_or_builder is a strict,
text-level AST-adjacent check with no exceptions). dashboard/*.py files also
must never import db.py directly (it can trigger schema migrations on a
read-only presentation page -- tests/test_dashboard.py's own structural
check), so this function owns opening/closing the real connection on the
page's behalf.

operational/cloud_snapshot_builder.py's own "real_today" section builder
calls this SAME function, so LOCAL mode and the published Cloud snapshot are
never two separately-derived sources of truth.
"""
from __future__ import annotations


def open_real_today_state(**kwargs) -> dict:
    from dashboard import real_today_view as rtv
    import db
    conn = db.get_conn()
    try:
        return rtv.build_real_today_state(conn, **kwargs)
    finally:
        conn.close()
