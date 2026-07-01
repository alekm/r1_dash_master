# R1 Dash Master

An MCP server that builds **importable RUCKUS One Data Studio dashboards** from a
simple declarative spec. Output is a `.zip` you import via **Data Studio → Settings
→ Import Dashboard**. Pure offline generation — no R1 API credentials needed.

📺 **Setup & usage in Claude Desktop:** https://youtu.be/-gU7yu6liOw

Data Studio is Apache Superset on an Apache Druid backend (`deployment: ALTO`). This
tool encodes the reverse-engineered dataset catalog and the chart/query grammar so you
(or an agent) can build valid dashboards without learning Superset internals or guessing
field names.

## Tools

- **`list_datasets()`** — all 18 R1 datasets (internal name, cube name, id, counts).
- **`describe_dataset(name)`** — exact metric + dimension names for one dataset.
- **`validate_spec(spec)`** — check a spec against the catalog before building.
- **`build_dashboard(spec, filename?)`** — emit an importable `.zip` (written to `out/`).

## Spec format

```jsonc
{
  "title": "Network Intelligence",      // generic — NEVER tenant-specific (bundles are portable across ECs)
  // tenant_id: OPTIONAL — omit it. Import auto-rescopes to the target EC (tenant). Only include to hard-pin a tenant.
  "time_range": "Last week",            // default for all charts (Last day/week/month/quarter, previous calendar week/month, or explicit range)
  "grain": "day",                       // OPTIONAL trend time grain: 30 second/minute/3·5·10·15·30 minute/hour/day/week/month/quarter (default hour); charts can override
  "rows": [                              // each row = list of charts; widths in a row sum to <= 12
    [ {chart}, {chart} ]
  ]
}
```

Chart:
```jsonc
{
  "type": "bignum" | "bignum_trend" | "line" | "bar" | "area" | "scatter" | "pie" | "table"
         | "gauge" | "heatmap" | "funnel" | "pivot" | "mixed" | "tree" | "bubble",
  "stacked": true,                       // bar/area only: stack the series
  "x": "apMac",                          // line/bar/area/scatter: optional DIMENSION x-axis (default __time)
  // pivot:  "rows": ["zoneName"], "columns": ["radio"], "metrics": [...]
  // mixed:  "metrics": [...] (bars) + "metrics_b": [...] (line) + optional "groupby"/"groupby_b","format_b"
  // tree:   "id": "apName", "parent": "apModel", "name": "apName", "metric": "..."
  // bubble: "entity": "apName", "x": <metric>, "y": <metric>, "size": <metric>  (x/y/size are METRICS here)
  // funnel/gauge/heatmap: "metric" (singular) + "groupby" ([dim]; heatmap uses first dim as Y)
  "dataset": "binnedSessions",          // internal name from list_datasets
  "title": "...", "width": 1-12,
  "metric":  "User Traffic (Total)"     // bignum/pie; string = saved metric
           | {"sql": "1.0*SUM(a)/SUM(b)", "label": "Rate"},  // or custom-SQL (ratios/%)
  "metrics": [ ... ],                    // line/table (list of the same forms)
  "groupby": ["radio"],
  "filter":  ["radio","5"]  | [["radio","5"],["zoneName","X"]],
  "time_range": "Last day",              // optional per-chart override
  "format": ".1%",                       // d3 number format (rates -> ".1%")
  "percent_of_total": ["Traffic (Total)"], // table: share-of-column-total column
  "row_limit": 25
}
```

## Layout & cross-filtering (design convention)

Data Studio dashboards are **cross-filterable**: clicking a value in any chart (e.g. a
venue in a venue table) filters the *entire* dashboard to that value; clearing it up top
removes the filter. So **put venue and AP tables/charts near the TOP** — they double as
interactive filter controls. Recommended order: KPI row → venue (and AP) table → detail
charts below. The builder preserves row order from the spec, so order your `rows` that way.

## Grammar notes baked in (gotchas)

- **Field names are exact & dataset-specific.** `radio` not `Radio`; `Unique Client MAC Count`
  not "Unique Client Count"; `User Traffic(Total)` (no space) in `sessionsSummary` vs
  `User Traffic (Total)` (space) in `binnedSessions`. `validate_spec` catches saved-metric/dim typos.
- **Custom-SQL metrics reference RAW columns** (e.g. `successCount`), not display metric names,
  and integer division floors — always `1.0 *` (or `100.0 *`). See `raw_columns` in the catalog.
- **Rate vs share:** a true rate = SQL metric + `.1%` format. `percent_of_total` (table
  `percent_metrics`) means "% of the column total" (contribution), not "format as %".
- **Band values are tri-band:** the `radio`/`band` dimensions take `"2.4"`, `"5"`, and
  `"6(5)"` — the 6 GHz band's literal value is the string `"6(5)"`, NOT `"6"` (confirmed from
  the per-band metric SQL). Per-band metrics label it `6(5) GHz`. Don't hardcode just 2.4/5.
- Dashboards are **transmutable across ECs** — keep titles generic, swap `tenant_id`.

## CLI (without MCP)

```bash
python3 builder.py examples/network_intelligence.json out/network_intelligence_IMPORT.zip
```

## Run as MCP

```bash
pip install -r requirements.txt
python3 server.py
```

**Easiest:** just ask Claude to set it up — point it at this repo and it'll wire the
MCP server into your client for you (that's what the [video](https://youtu.be/-gU7yu6liOw) shows).

**Manual:** register it yourself. For **Claude Desktop** (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "r1-dash-master": {
      "command": "python3",
      "args": ["/path/to/r1_dash_master/server.py"]
    }
  }
}
```

## Examples vs. Gallery

- **`examples/*.json`** — source specs, for driving the MCP/builder and learning the spec format.
- **`gallery/*.zip`** — prebuilt, **ready-to-import** dashboards. Since bundles are tenant-less,
  they auto-rescope to whatever EC you import them into. Grab one → Data Studio → Settings →
  Import Dashboard. Current set: executive_overview, capacity_rf, connection_health,
  network_intelligence, switch_health, chart_gallery.
- Regenerate the gallery from specs anytime: **`./build_gallery.sh`** (keeps zips in sync).

## Status

Catalog: 18/19 datasets mapped (AP Alarms & Controller Inventory are SmartZone-only, N/A in R1).
Viz (15): bignum, bignum_trend, line, bar, area, scatter, pie, table, gauge, heatmap, funnel, pivot, mixed, tree, bubble. Query grammar: saved +
custom-SQL metrics, percent-of-total, dimension + time filters, d3 formats. Cross-filtering is
built in (click a chart value to filter the dashboard). Not yet: explicit dashboard-level native
filter bar; remaining viz (treemap, sunburst, box plot, radar, waterfall, graph, histogram,
calendar heatmap, sankey, smooth/stepped line); auto-import (needs an analytics-backend API — import the zip via UI).
