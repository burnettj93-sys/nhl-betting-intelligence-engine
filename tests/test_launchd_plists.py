"""Every launchd job definition kept in the repository must be well-formed XML: launchd rejects a plist whose comments contain a double hyphen, and the repository copy is what an
install or a migration starts from (found 2026-10-17: seven copies were invalid while the installed ones were fine)."""
import glob
import plistlib
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


class TestPlists(unittest.TestCase):
    def test_every_plist_in_the_repository_parses(self):
        files = sorted(glob.glob(str(REPO / "deploy" / "launchd" / "*.plist")) + glob.glob(str(REPO / "operational" / "*.plist")))
        self.assertGreaterEqual(len(files), 10)
        for f in files:
            with self.subTest(plist=Path(f).name):
                d = plistlib.loads(Path(f).read_bytes())
                self.assertTrue(d["Label"].startswith("com.nhlengine."))
                self.assertTrue(d.get("StartInterval") or d.get("StartCalendarInterval"), "a job with no schedule never runs")


if __name__ == "__main__":
    unittest.main()
