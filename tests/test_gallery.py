"""gallery/ zips are build products of examples/*.json and are committed to the repo.
They drifted once already: a catalog fix landed in the spec but the zips were never
rebuilt, so the gallery kept shipping the bug. This fails when they disagree.
Fix a failure by running ./build_gallery.sh and committing the result.
"""
import glob, hashlib, json, os, sys, tempfile, unittest, zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import builder  # noqa: E402

GALLERIES = {"r1": "gallery", "analytics": os.path.join("gallery", "analytics")}


def _contents(path):
    with zipfile.ZipFile(path) as z:
        return {i.filename: hashlib.md5(z.read(i)).hexdigest() for i in z.infolist()}


class GalleryIsCurrent(unittest.TestCase):
    def test_each_zip_matches_its_spec(self):
        specs = sorted(glob.glob(os.path.join(ROOT, "examples", "*.json")))
        self.assertTrue(specs, "no example specs found")
        for target, gdir in GALLERIES.items():
            catalog = builder.load_catalog(target)
            for spec_path in specs:
                name = os.path.basename(spec_path)[:-len(".json")]
                with self.subTest(target=target, board=name):
                    shipped = os.path.join(ROOT, gdir, f"{name}.zip")
                    self.assertTrue(os.path.exists(shipped), f"{gdir}/{name}.zip is missing")
                    spec = {**json.load(open(spec_path)), "target": target}
                    fd, tmp = tempfile.mkstemp(suffix=".zip")
                    os.close(fd)
                    try:
                        builder.build_dashboard(spec, catalog, tmp)
                        self.assertEqual(_contents(shipped), _contents(tmp),
                                         f"{gdir}/{name}.zip is stale — run ./build_gallery.sh")
                    finally:
                        os.remove(tmp)


if __name__ == "__main__":
    unittest.main(verbosity=2)
