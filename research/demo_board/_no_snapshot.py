"""The simulated board has no snapshot-backed variant: the hosted app never runs it. This stands in for the module the code used to
branch on, so the (unreachable) snapshot branches in the research harness cannot reach published data."""


class cloud_snapshot:  # noqa: N801 - mirrors the module name the harness code references
    SnapshotUnavailable = RuntimeError

    @staticmethod
    def snapshot_active() -> bool:
        return False
