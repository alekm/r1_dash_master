"""Regression tests for issue #1 — reserved chars in titles must not corrupt
the bundle (NTFS Alternate Data Stream on Windows). Run: python3 -m unittest -v
from the repo root, or `python3 tests/test_filenames.py`."""
import json, os, sys, tempfile, unittest, zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import builder  # noqa: E402

CATALOG = json.load(open(os.path.join(ROOT, "catalog.json")))


def _build(spec):
    fd, path = tempfile.mkstemp(suffix=".zip")
    os.close(fd)
    builder.build_dashboard(spec, CATALOG, path)
    return path


def _charts(zip_path):
    with zipfile.ZipFile(zip_path) as zf:
        return [i for i in zf.infolist() if "/charts/" in i.filename]


class SafeFilename(unittest.TestCase):
    def test_reserved_chars_stripped(self):
        for ch in '<>:"/\\|?*':
            self.assertNotIn(ch, builder._safe_filename(f"a{ch}b"))

    def test_control_chars_stripped(self):
        self.assertEqual(builder._safe_filename("a\x00\tb"), "a_b")

    def test_empty_slug_falls_back(self):
        self.assertEqual(builder._safe_filename(':::'), "chart")
        self.assertEqual(builder._safe_filename('   '), "chart")


class BundleEmission(unittest.TestCase):
    def test_colon_title_yields_nonempty_yaml(self):
        # The reported repro: a colon in the title must NOT empty the chart.
        spec = {"title": "Repro", "rows": [[
            {"type": "bignum", "dataset": "binnedSessions",
             "metric": "Session Count", "title": "sS saved: Session Count"}]]}
        charts = _charts(_build(spec))
        self.assertEqual(len(charts), 1)
        self.assertTrue(charts[0].filename.endswith(".yaml"), charts[0].filename)
        self.assertNotIn(":", charts[0].filename)
        self.assertGreater(charts[0].file_size, 0)

    def test_same_slug_titles_stay_distinct(self):
        # "A/B" and "A:B" both slugify to "AB" — the appended sid must keep them
        # as two separate, non-empty files rather than colliding/overwriting.
        spec = {"title": "Collide", "rows": [[
            {"type": "bignum", "dataset": "binnedSessions", "metric": "Session Count", "title": "A/B"},
            {"type": "bignum", "dataset": "binnedSessions", "metric": "Session Count", "title": "A:B"}]]}
        charts = _charts(_build(spec))
        names = [c.filename for c in charts]
        self.assertEqual(len(names), 2)
        self.assertEqual(len(set(names)), 2)
        for c in charts:
            self.assertGreater(c.file_size, 0)

    def test_empty_chart_file_raises(self):
        # Belt-and-suspenders: a 0-byte chart must abort the build, not succeed.
        spec = {"title": "Empty", "rows": [[
            {"type": "bignum", "dataset": "binnedSessions",
             "metric": "Session Count", "title": "X"}]]}
        orig = builder._chart_yaml
        builder._chart_yaml = lambda *a, **k: ""
        try:
            with self.assertRaises((RuntimeError, ValueError)):
                _build(spec)
        finally:
            builder._chart_yaml = orig


if __name__ == "__main__":
    unittest.main(verbosity=2)
