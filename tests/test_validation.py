"""Spec-validation guards: row geometry, spec shape, and the deployment marker.

Row overflow and the MLISA/ALTO marker both fail SILENTLY at import time (charts
vanish; the bundle is rejected or mis-bound), so they are checked here rather than
left to a human noticing a missing panel.
Run: python3 -m unittest discover -s tests
"""
import json, os, sys, tempfile, unittest, zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import builder  # noqa: E402

CATALOG = json.load(open(os.path.join(ROOT, "catalog.json")))
CHART = {"type": "bignum", "dataset": "binnedSessions", "metric": "Session Count"}


def _spec(rows, **kw):
    return {"title": "T", "rows": rows, **kw}


class RowGeometry(unittest.TestCase):
    def test_row_over_twelve_columns_is_rejected(self):
        # Superset drops the overflow with no error: the charts are in the zip but
        # absent from the imported dashboard. Observed on a 4x width-4 row.
        row = [dict(CHART, title=str(i), width=4) for i in range(4)]
        problems = builder.validate_spec(_spec([row]), CATALOG)
        self.assertTrue(any("12-column grid" in p for p in problems), problems)

    def test_row_of_exactly_twelve_is_fine(self):
        row = [dict(CHART, title=str(i), width=3) for i in range(4)]
        self.assertEqual(builder.validate_spec(_spec([row]), CATALOG), [])

    def test_default_width_counts_toward_the_total(self):
        # width defaults to 4, so four undeclared charts already overflow.
        row = [dict(CHART, title=str(i)) for i in range(4)]
        problems = builder.validate_spec(_spec([row]), CATALOG)
        self.assertTrue(any("12-column grid" in p for p in problems), problems)

    def test_shipped_examples_all_fit_the_grid(self):
        import glob
        for f in sorted(glob.glob(os.path.join(ROOT, "examples", "*.json"))):
            with self.subTest(example=os.path.basename(f)):
                self.assertEqual(builder.validate_spec(json.load(open(f)), CATALOG), [])


class SpecShape(unittest.TestCase):
    def test_unwrapped_chart_reports_instead_of_raising(self):
        problems = builder.validate_spec(_spec([dict(CHART, title="t")]), CATALOG)
        self.assertTrue(any("must be a LIST" in p for p in problems), problems)

    def test_non_dict_chart_reports_instead_of_raising(self):
        problems = builder.validate_spec(_spec([["nope"]]), CATALOG)
        self.assertTrue(any("must be a chart object" in p for p in problems), problems)


class DeploymentMarker(unittest.TestCase):
    def _metadata(self, catalog):
        fd, path = tempfile.mkstemp(suffix=".zip")
        os.close(fd)
        try:
            builder.build_dashboard(_spec([[dict(CHART, title="x")]]), catalog, path)
            with zipfile.ZipFile(path) as z:
                return z.read("export/metadata.yaml").decode()
        finally:
            os.remove(path)

    def test_marker_comes_from_the_catalog(self):
        # ALTO = RUCKUS One, MLISA = RUCKUS Analytics. Hardcoding it made every
        # bundle claim ALTO regardless of which product the catalog described.
        self.assertIn("deployment: ALTO", self._metadata(CATALOG))
        self.assertIn("deployment: MLISA", self._metadata({**CATALOG, "deployment": "MLISA"}))


class Targets(unittest.TestCase):
    def test_every_target_has_a_loadable_tagged_catalog(self):
        for target in builder.TARGETS:
            with self.subTest(target=target):
                cat = builder.load_catalog(target)
                self.assertEqual(cat["target"], target)
                self.assertTrue(cat["datasets"])

    def test_unknown_target_is_rejected(self):
        with self.assertRaises(ValueError):
            builder.load_catalog("smartzone")

    def test_spec_for_one_product_is_refused_by_the_others_catalog(self):
        # The failure this guards: identical metric names mean a wrong-target spec
        # otherwise validates clean and then binds to the other product's ids.
        spec = _spec([[dict(CHART, title="x")]], target="r1")
        problems = builder.validate_spec(spec, builder.load_catalog("analytics"))
        self.assertTrue(any("catalog is for 'analytics'" in p for p in problems), problems)

    def test_deployment_marker_follows_the_target(self):
        for target, marker in (("r1", "ALTO"), ("analytics", "MLISA")):
            with self.subTest(target=target):
                self.assertEqual(builder.load_catalog(target)["deployment"], marker)

    def test_every_dataset_carries_a_uuid_and_they_are_unique(self):
        # A chart binds on dataset_uuid AND datasource id; import 500s if either
        # fails to resolve (verified on Analytics with a right-id/wrong-uuid probe).
        for target in builder.TARGETS:
            with self.subTest(target=target):
                ds = builder.load_catalog(target)["datasets"]
                uuids = [d.get("dataset_uuid") for d in ds]
                self.assertTrue(all(uuids), [d["name"] for d in ds if not d.get("dataset_uuid")])
                self.assertEqual(len(uuids), len(set(uuids)))


if __name__ == "__main__":
    unittest.main(verbosity=2)
