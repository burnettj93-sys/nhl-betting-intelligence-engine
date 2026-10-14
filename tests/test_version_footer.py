"""The public footer says which code and which data a page is, and says so when they differ."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from dashboard import version as v


class TestCodeCommit(unittest.TestCase):
    def repo(self, head, files=None, packed=None):
        d = Path(tempfile.mkdtemp())
        (d / ".git").mkdir()
        (d / ".git" / "HEAD").write_text(head)
        for name, text in (files or {}).items():
            p = d / ".git" / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text)
        if packed:
            (d / ".git" / "packed-refs").write_text(packed)
        return d

    def test_a_loose_ref_a_packed_ref_and_a_detached_head_are_read(self):
        sha = "04a20dd91884b4adb8bc8ca180ea6d3d125e384b"
        self.assertEqual(v.code_commit(self.repo("ref: refs/heads/master\n", {"refs/heads/master": sha + "\n"})), sha[:12])
        self.assertEqual(v.code_commit(self.repo("ref: refs/heads/master\n", packed=f"# pack-refs\n{sha} refs/heads/master\n")), sha[:12])
        self.assertEqual(v.code_commit(self.repo(sha + "\n")), sha[:12])

    def test_no_git_directory_is_none_not_an_error(self):
        self.assertIsNone(v.code_commit(Path(tempfile.mkdtemp())))

    def test_the_footer_names_both_and_flags_a_mismatch(self):
        self.assertEqual(v.footer_text("04a20dd91884", "04a20dd91884"), "Code 04a20dd · data built by 04a20dd")
        self.assertIn("different versions", v.footer_text("04a20dd91884", "9fa8a6d354aa"))
        self.assertEqual(v.footer_text(None, None), "")
        self.assertEqual(v.data_commit({"metadata": {"source_master_commit": "abc"}}), "abc")
        self.assertIsNone(v.data_commit(None))


if __name__ == "__main__":
    unittest.main()


class TestDollarSignsInHtmlBlocks(unittest.TestCase):
    """2026-10-13, a page on a phone: "its own \\$500" showed its backslash because the text sits inside an HTML block, where markdown escapes are not processed."""
    def _rendered(self, fn, *a):
        from unittest import mock
        with mock.patch("streamlit.markdown") as md:
            fn(*a)
        return md.call_args.args[0]

    def test_banner_shows_a_plain_dollar_amount(self):
        from dashboard import ui
        html = self._rendered(ui.banner, "Each account starts with its own \\$500 of paper money, and $10 a ticket.", "info")
        self.assertNotIn("\\", html)
        self.assertIn("&#36;500", html)
        self.assertIn("&#36;10", html)

    def test_empty_state_shows_a_plain_dollar_amount(self):
        from dashboard import theme
        html = self._rendered(theme.empty_state, "Stake \\$10 only", "or $5")
        self.assertNotIn("\\", html)
        self.assertIn("&#36;10", html)
