"""gallery/*.zip are build products of examples/*.json and are committed to the repo.
They drifted once already: a catalog fix landed in the spec but the zips were never
rebuilt, so the gallery kept shipping the bug. This fails when they disagree.
Fix a failure by running ./build_gallery.sh and committing the result.
"""
import glob, hashlib, json, os, sys, tempfile, unittest, zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import builder  # noqa: E402

CATALOG = json.load(open(os.path.join(ROOT, "catalog.json")))


def _contents(path):
    with zipfile.ZipFile(path) as z:
        return {i.filename: hashlib.md5(z.read(i)).hexdigest() for i in z.infolist()}


class GalleryIsCurrent(unittest.TestCase):
    def test_each_zip_matches_its_spec(self):
        specs = sorted(glob.glob(os.path.join(ROOT, "examples", "*.json")))
        self.assertTrue(specs, "no example specs found")
        for spec_path in specs:
            name = os.path.basename(spec_path)[:-len(".json")]
            with self.subTest(board=name):
                shipped = os.path.join(ROOT, "gallery", f"{name}.zip")
                self.assertTrue(os.path.exists(shipped), f"gallery/{name}.zip is missing")
                fd, tmp = tempfile.mkstemp(suffix=".zip")
                os.close(fd)
                try:
                    builder.build_dashboard(json.load(open(spec_path)), CATALOG, tmp)
                    self.assertEqual(_contents(shipped), _contents(tmp),
                                     f"gallery/{name}.zip is stale — run ./build_gallery.sh")
                finally:
                    os.remove(tmp)


if __name__ == "__main__":
    unittest.main(verbosity=2)
