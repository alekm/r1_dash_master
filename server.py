#!/usr/bin/env python3
"""R1 Dash Master — MCP server that builds importable Data Studio (Superset)
dashboard bundles from a declarative spec, for RUCKUS One or RUCKUS Analytics.

Pure offline generation: tools return / write a .zip you import via
Data Studio > Settings > Import Dashboard. No API auth required.

Every call needs a target ('r1' or 'analytics'): the spec's own 'target' key, the
tool's target argument, or the R1DM_TARGET environment variable as a default.
"""
import json
import os
from pathlib import Path

from mcp.server.mcpserver import MCPServer

import builder

HERE = Path(__file__).parent
CATALOGS = {t: builder.load_catalog(t) for t in builder.TARGETS}
PRODUCT = {"r1": "RUCKUS One", "analytics": "RUCKUS Analytics"}
# Optional default for single-product setups. Deliberately no built-in default:
# a spec built for the wrong product binds to the wrong datasets.
DEFAULT_TARGET = os.environ.get("R1DM_TARGET", "")
OUT_DIR = Path(os.environ.get("R1DM_OUT_DIR", HERE / "out"))
OUT_DIR.mkdir(parents=True, exist_ok=True)

mcp = MCPServer("r1-dash-master")


def _resolve(target):
    """(target, catalog, None) or (None, None, error message)."""
    t = target or DEFAULT_TARGET
    if t in CATALOGS:
        return t, CATALOGS[t], None
    why = f"unknown target {t!r}" if t else "no target given"
    return None, None, (f"ERROR: {why}. Say which product the dashboard is for: "
                        f"target = {' | '.join(repr(k) for k in CATALOGS)} "
                        f"({', '.join(f'{k} = {v}' for k, v in PRODUCT.items())}). "
                        "Dataset ids and UUIDs differ between them.")


@mcp.tool()
def list_datasets(target: str = "") -> str:
    """List the Data Studio datasets available for dashboards on one product.

    Args:
        target: 'r1' (RUCKUS One) or 'analytics' (RUCKUS Analytics). The two
                products carry different datasets and different identifiers.

    Returns each dataset's internal name (used in specs), display/cube name,
    datasource id, metric count, and dimension count.
    """
    t, catalog, err = _resolve(target)
    if err:
        return err
    lines = [f"{PRODUCT[t]} Data Studio datasets (use 'name' in specs, target {t!r}):", ""]
    for d in catalog["datasets"]:
        lines.append(f"- {d['name']}  (\"{d['display']}\", id {d['datasource_id']}) "
                     f"— {len(d['metrics'])} metrics, {len(d['dims'])} dims"
                     + (f" — {d['notes']}" if d.get("notes") else ""))
    if catalog.get("not_in_r1"):
        lines += ["", "Not available in R1: " + "; ".join(catalog["not_in_r1"])]
    return "\n".join(lines)


@mcp.tool()
def describe_dataset(name: str, target: str = "") -> str:
    """Get the exact metric and dimension names for one dataset.

    Args:
        name: internal dataset name (e.g. 'binnedSessions', 'mlisa-apConnectionStats').
              Accepts the display name too.
        target: 'r1' or 'analytics'. Names mostly match across products but not
                entirely (Analytics has controller dims, R1 has tag/tagList).
    """
    t, catalog, err = _resolve(target)
    if err:
        return err
    global_labels = catalog.get("dim_labels", {})
    for d in catalog["datasets"]:
        if name in (d["name"], d["display"]):
            out = {k: d[k] for k in ("name", "display", "datasource_id", "dataset_uuid",
                                     "metrics", "dims") }
            # per-dataset labels take precedence; fall back to the global map
            labels = {**global_labels, **d.get("labels", {})}
            # show each dim as "internal (Data Studio label)" when a label is known
            out["dims_labeled"] = [
                f"{dim} ({labels[dim]})" if labels.get(dim) else dim
                for dim in d["dims"]
            ]
            for opt in ("notes", "raw_columns", "metric_sql"):
                if d.get(opt):
                    out[opt] = d[opt]
            return json.dumps(out, indent=2)
    names = ", ".join(d["name"] for d in catalog["datasets"])
    return f"ERROR: dataset {name!r} not found in {t!r}. Available: {names}"


@mcp.tool()
def describe_chart_types() -> str:
    """List every valid chart 'type' and the keys each one requires/accepts.

    Use this before writing a spec so you don't guess type names or field shapes.
    KEY RULE: 'metric' = ONE metric (a string or {"sql","label"}); 'metrics' = a
    LIST of them. Each type wants one or the other — mixing them is the most common
    error, and validate_spec now catches it.
    """
    lines = ["Chart types (set as chart 'type'). metric = single; metrics = list:", ""]
    for name, spec in builder.CHART_TYPES.items():
        lines.append(f"- {name}: {spec['desc']}")
        lines.append(f"    required: {', '.join(spec['required'])}"
                     + (f"   optional: {', '.join(spec['optional'])}" if spec.get("optional") else ""))
    lines += ["",
              "A metric is a saved-metric name (string) OR a custom-SQL metric "
              '{"sql": "1.0*SUM(a)/SUM(b)", "label": "Rate"}.',
              "Common chart keys: dataset (required), title, width (1-12), time_range, format (d3), filter."]
    return "\n".join(lines)


@mcp.tool()
def validate_spec(spec: dict) -> str:
    """Validate a dashboard spec against the catalog WITHOUT building.

    Checks dataset names, saved-metric names, groupby/filter dimension names,
    against the catalog of the spec's 'target' ('r1' or 'analytics').
    Returns 'OK' or a list of problems. Always run this before build_dashboard
    when unsure of field names.
    """
    t, catalog, err = _resolve(spec.get("target"))
    if err:
        return err
    problems = builder.validate_spec({**spec, "target": t}, catalog)
    if not problems:
        return "OK — spec is valid."
    return "PROBLEMS:\n  - " + "\n  - ".join(problems)


@mcp.tool()
def build_dashboard(spec: dict, filename: str = "") -> str:
    """Build an importable Data Studio dashboard .zip from a spec.

    The spec is a dict with: target ('r1' = RUCKUS One or 'analytics' = RUCKUS
    Analytics; REQUIRED unless R1DM_TARGET is set), title (generic name, NOT
    tenant-specific), optional tenant_id (the EC), optional time_range, and rows (list of rows; each
    row a list of chart dicts). Call describe_chart_types() for the full list of
    types and their required keys.

    Each chart needs 'type' and 'dataset'. KEY GOTCHA — 'metric' vs 'metrics':
    bignum/bignum_trend/pie/gauge/heatmap/funnel/tree take 'metric' (a SINGLE
    metric); line/bar/area/scatter/table/pivot/mixed take 'metrics' (a LIST);
    bubble takes 'entity'+'x'+'y'+'size'. A metric is a saved-metric name (string)
    or a custom-SQL metric {"sql": "1.0*SUM(a)/SUM(b)", "label": "..."}. Also
    supports percent-of-total (table), dimension + time filters, d3 number formats.
    validate_spec now checks per-type required keys, so a clean validate means build
    will not raise a KeyError.

    Args:
        spec: the dashboard spec dict.
        filename: optional output filename (defaults to <title>_<target>_IMPORT.zip).

    Returns the output path and a summary, or validation errors.
    """
    t, catalog, err = _resolve(spec.get("target"))
    if err:
        return err
    spec = {**spec, "target": t}
    # The target is in the default name so a bundle on disk says which product it is for.
    stem = filename or f"{spec.get('title', 'dashboard')}_{t}_IMPORT"
    if stem.lower().endswith(".zip"):
        stem = stem[:-4]
    # filename is caller-supplied: reuse the builder's slug so a path separator or a
    # '..' can't escape OUT_DIR (build_dashboard unlinks out_path before writing).
    out = (OUT_DIR / f"{builder._safe_filename(stem)}.zip").resolve()
    if out.parent != OUT_DIR.resolve():
        return f"BUILD FAILED: refusing to write outside {OUT_DIR}"
    out_path = str(out)
    try:
        summary = builder.build_dashboard(spec, catalog, out_path)
    except ValueError as e:
        return f"BUILD FAILED:\n{e}"
    return (f"Built {summary['charts']} charts for {PRODUCT[t]} -> {summary['output']}\n"
            f"Datasets used: {', '.join(summary['datasets'])}\n"
            f"Import via Data Studio > Settings > Import Dashboard.")


if __name__ == "__main__":
    mcp.run()
