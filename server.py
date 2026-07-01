#!/usr/bin/env python3
"""R1 Dash Master — MCP server that builds importable RUCKUS One Data Studio
(Superset) dashboard bundles from a declarative spec.

Pure offline generation: tools return / write a .zip you import via
Data Studio > Settings > Import Dashboard. No R1 API auth required.
"""
import json
import os
from pathlib import Path

from mcp.server.fastmcp import FastMCP

import builder

HERE = Path(__file__).parent
CATALOG = json.load(open(HERE / "catalog.json"))
OUT_DIR = Path(os.environ.get("R1DM_OUT_DIR", HERE / "out"))
OUT_DIR.mkdir(exist_ok=True)

mcp = FastMCP("r1-dash-master")


@mcp.tool()
def list_datasets() -> str:
    """List all RUCKUS One Data Studio datasets available for dashboards.

    Returns each dataset's internal name (used in specs), display/cube name,
    datasource id, metric count, and dimension count.
    """
    lines = ["RUCKUS One Data Studio datasets (use 'name' in specs):", ""]
    for d in CATALOG["datasets"]:
        lines.append(f"- {d['name']}  (\"{d['display']}\", id {d['datasource_id']}) "
                     f"— {len(d['metrics'])} metrics, {len(d['dims'])} dims"
                     + (f" — {d['notes']}" if d.get("notes") else ""))
    lines.append("")
    lines.append("Not available in R1: " + "; ".join(CATALOG.get("not_in_r1", [])))
    return "\n".join(lines)


@mcp.tool()
def describe_dataset(name: str) -> str:
    """Get the exact metric and dimension names for one dataset.

    Args:
        name: internal dataset name (e.g. 'binnedSessions', 'mlisa-apConnectionStats').
              Accepts the display name too.
    """
    global_labels = CATALOG.get("dim_labels", {})
    for d in CATALOG["datasets"]:
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
            for opt in ("notes", "raw_columns"):
                if d.get(opt):
                    out[opt] = d[opt]
            return json.dumps(out, indent=2)
    names = ", ".join(d["name"] for d in CATALOG["datasets"])
    return f"ERROR: dataset {name!r} not found. Available: {names}"


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

    Checks dataset names, saved-metric names, groupby/filter dimension names.
    Returns 'OK' or a list of problems. Always run this before build_dashboard
    when unsure of field names.
    """
    problems = builder.validate_spec(spec, CATALOG)
    if not problems:
        return "OK — spec is valid."
    return "PROBLEMS:\n  - " + "\n  - ".join(problems)


@mcp.tool()
def build_dashboard(spec: dict, filename: str = "") -> str:
    """Build an importable Data Studio dashboard .zip from a spec.

    The spec is a dict with: title (generic name, NOT tenant-specific),
    optional tenant_id (the EC), optional time_range, and rows (list of rows; each
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
        filename: optional output filename (defaults to <title>_IMPORT.zip).

    Returns the output path and a summary, or validation errors.
    """
    if not filename:
        filename = (spec.get("title", "dashboard").replace(" ", "_") + "_IMPORT.zip")
    out_path = str(OUT_DIR / filename)
    try:
        summary = builder.build_dashboard(spec, CATALOG, out_path)
    except ValueError as e:
        return f"BUILD FAILED:\n{e}"
    return (f"Built {summary['charts']} charts -> {summary['output']}\n"
            f"Datasets used: {', '.join(summary['datasets'])}\n"
            f"Import via Data Studio > Settings > Import Dashboard.")


if __name__ == "__main__":
    mcp.run()
