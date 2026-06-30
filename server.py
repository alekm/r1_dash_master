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
    tenant_id (the EC), optional time_range, and rows (list of rows; each row a
    list of chart dicts). See SPEC.md / examples for chart shapes. Supports
    big-number / line / pie / table; saved metrics; custom-SQL metrics
    ({"sql": "1.0*SUM(a)/SUM(b)", "label": "..."}); percent-of-total (table);
    dimension + time filters; d3 number formats.

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
