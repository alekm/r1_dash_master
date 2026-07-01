"""R1 Dash Master — build importable RUCKUS One Data Studio (Superset) dashboard
bundles from a declarative spec. Offline; output is a .zip imported via
Data Studio > Settings > Import Dashboard. Spec format: see README.md."""
import json, os, uuid, zipfile, shutil

# Fixed namespace -> deterministic uuid5 so re-imports update a board in place.
_NS = uuid.UUID("6f1d4b2a-9c3e-5a7f-8b21-d1da54a00000")


def _stable_uuid(*parts):
    return str(uuid.uuid5(_NS, "|".join(str(p) for p in parts)))

HEIGHT = {"bignum": 30}
DEFAULT_HEIGHT = 50


def _sqlmetric(spec_metric, sid, idx):
    """Return a metric usable in metrics[]: a saved-metric string, or an adhoc-SQL dict."""
    if isinstance(spec_metric, dict) and "sql" in spec_metric:
        return {
            "expressionType": "SQL",
            "sqlExpression": spec_metric["sql"],
            "column": None, "aggregate": None, "hasCustomLabel": True,
            "label": spec_metric.get("label", spec_metric["sql"]),
            "optionName": f"metric_{sid}_{idx}",
        }
    return spec_metric  # plain saved-metric name


def _mlabel(m):
    return m["label"] if isinstance(m, dict) else m


def _norm_filters(chart):
    f = chart.get("filter")
    if not f:
        return []
    if f and isinstance(f[0], str):  # single ["col","val"]
        return [f]
    return f  # list of [col,val]


def _tfilter(tr, sid):
    return {"expressionType": "SIMPLE", "subject": "__time", "operator": "TEMPORAL_RANGE",
            "comparator": tr, "clause": "WHERE", "sqlExpression": None, "isExtra": False,
            "isNew": False, "datasourceWarning": False, "filterOptionName": f"tfilter_{sid}"}


def _dfilter(col, val, sid):
    # list/tuple value -> IN filter; scalar -> equality
    op = "IN" if isinstance(val, (list, tuple)) else "=="
    return {"expressionType": "SIMPLE", "subject": col, "operator": op, "comparator": val,
            "clause": "WHERE", "sqlExpression": None, "isExtra": False, "isNew": False,
            "datasourceWarning": False, "filterOptionName": f"dfilter_{sid}_{col}"}


def _adhoc_filters(chart, sid, tr):
    out = [_tfilter(tr, sid)]
    for col, val in _norm_filters(chart):
        out.append(_dfilter(col, val, sid))
    return out


def _chart_yaml(viz, params, qc, sid, ch_uuid, ds_uuid):
    return (
        f"slice_name: {json.dumps(params['_slice_name'])}\n"
        "description: null\ncertified_by: null\ncertification_details: null\n"
        f"viz_type: {viz}\n"
        f"params: {json.dumps({k: v for k, v in params.items() if k != '_slice_name'})}\n"
        f"query_context: {json.dumps(json.dumps(qc))}\n"
        "cache_timeout: null\n"
        f"uuid: {ch_uuid}\nversion: 1.0.0\n"
        f"dataset_uuid: {ds_uuid}\nchartId: {sid}\n"
    )


# Time-grain labels (from the Data Studio UI) -> Superset ISO time_grain_sqla codes.
# NOTE: minute is PT1M, month is P1M (T-prefix = time-of-day duration).
_GRAINS = {
    "30 second": "PT30S", "30s": "PT30S",
    "minute": "PT1M", "1 minute": "PT1M", "1m": "PT1M",
    "3 minute": "PT3M", "5 minute": "PT5M", "10 minute": "PT10M",
    "15 minute": "PT15M", "30 minute": "PT30M",
    "hour": "PT1H", "hourly": "PT1H", "h": "PT1H",
    "day": "P1D", "daily": "P1D", "d": "P1D",
    "week": "P1W", "weekly": "P1W", "w": "P1W",
    "month": "P1M", "monthly": "P1M",
    "quarter": "P3M", "quarterly": "P3M",
}


def _grain(chart):
    """Resolve a chart/spec 'grain' (UI label or token) to a Superset time_grain_sqla
    code. Defaults to hourly (PT1H). Accepts an ISO code passed through verbatim."""
    raw = chart.get("grain") or chart.get("_grain")
    if not raw:
        return "PT1H"
    key = str(raw).strip().lower()
    if key in _GRAINS:
        return _GRAINS[key]
    if raw.startswith(("PT", "P")):  # already an ISO code
        return raw
    return "PT1H"


def _build_chart(chart, ds, tenant, dash_id, sid):
    n = ds["datasource_id"]
    tr = chart.get("time_range") or chart["_time_range"]
    typ = chart["type"]
    g = _grain(chart)
    base = {"datasource": f"{n}__table", "slice_id": sid, "_slice_name": chart["title"],
            "datasource_name": ds["name"], "extra_form_data": {}, "dashboards": [dash_id]}
    if tenant:
        base["tenant_ids"] = [tenant]
    afilt = _adhoc_filters(chart, sid, tr)

    if typ == "bignum":
        m = _sqlmetric(chart["metric"], sid, 0)
        qc = {"datasource": {"id": n, "type": "table"}, "force": False, "queries": [{
            "filters": [{"col": "__time", "op": "TEMPORAL_RANGE", "val": tr}],
            "extras": {"having": "", "where": ""}, "applied_time_extras": {}, "columns": [],
            "metrics": [m], "annotation_layers": [], "series_limit": 0, "order_desc": True,
            "url_params": {}, "custom_params": {}, "custom_form_data": {}}],
            "form_data": {**base, "viz_type": "big_number_total", "metric": m, "adhoc_filters": afilt,
                          "header_font_size": 0.4, "subheader_font_size": 0.15,
                          "y_axis_format": chart.get("format", "SMART_NUMBER"), "time_format": "smart_date"},
            "result_format": "json", "result_type": "full"}
        p = {**base, "viz_type": "big_number_total", "metric": m, "adhoc_filters": afilt,
             "header_font_size": 0.4, "subheader_font_size": 0.15,
             "y_axis_format": chart.get("format", "SMART_NUMBER"), "time_format": "smart_date",
             "conditional_formatting": []}
        return "big_number_total", p, qc

    if typ in ("line", "bar", "area", "scatter"):
        viz = {"line": "echarts_timeseries_line", "bar": "echarts_timeseries_bar",
               "area": "echarts_area", "scatter": "echarts_timeseries_scatter"}[typ]
        ms = [_sqlmetric(m, sid, i) for i, m in enumerate(chart["metrics"])]
        gb = chart.get("groupby", [])
        piv = {_mlabel(m): {"operator": "mean"} for m in ms}
        xcol = chart.get("x")  # optional dimension x-axis (default: time)
        if xcol:
            base_axis = {"columnType": "BASE_AXIS", "sqlExpression": xcol, "label": xcol, "expressionType": "SQL"}
            xaxis, index, qextras = xcol, [xcol], {"having": "", "where": ""}
        else:
            base_axis = {"timeGrain": g, "columnType": "BASE_AXIS", "sqlExpression": "__time",
                         "label": "__time", "expressionType": "SQL"}
            xaxis, index, qextras = "__time", ["__time"], {"time_grain_sqla": g, "having": "", "where": ""}
        qc = {"datasource": {"id": n, "type": "table"}, "force": False, "queries": [{
            "filters": [{"col": "__time", "op": "TEMPORAL_RANGE", "val": tr}],
            "extras": qextras, "applied_time_extras": {},
            "columns": [base_axis] + list(gb),
            "metrics": ms, "orderby": [[ms[0], False]], "annotation_layers": [], "row_limit": 10000,
            "series_columns": gb, "series_limit": 0, "order_desc": True, "url_params": {},
            "custom_params": {}, "custom_form_data": {}, "time_offsets": [],
            "post_processing": [{"operation": "pivot", "options": {"index": index, "columns": gb,
                                "aggregates": piv, "drop_missing_columns": False}}, {"operation": "flatten"}]}],
            "form_data": {**base, "viz_type": viz, "x_axis": xaxis,
                          "metrics": ms, "groupby": gb, "adhoc_filters": afilt,
                          "row_limit": 10000, "color_scheme": "acxColor",
                          "show_legend": True, "y_axis_format": chart.get("format", "SMART_NUMBER")},
            "result_format": "json", "result_type": "full"}
        if not xcol:
            qc["form_data"]["time_grain_sqla"] = g
        p = {**base, "viz_type": viz, "x_axis": xaxis,
             "x_axis_sort_asc": True, "x_axis_sort_series": "name", "x_axis_sort_series_ascending": True,
             "metrics": ms, "groupby": gb, "adhoc_filters": afilt, "order_desc": True, "row_limit": 10000,
             "truncate_metric": True, "show_empty_columns": True, "comparison_type": "values",
             "annotation_layers": [], "color_scheme": "acxColor",
             "only_total": (len(gb) == 0), "show_legend": True,
             "legendType": "scroll", "legendOrientation": "top", "x_axis_time_format": "smart_date",
             "rich_tooltip": True, "showTooltipTotal": True,
             "y_axis_format": chart.get("format", "SMART_NUMBER"), "truncateXAxis": True,
             "y_axis_bounds": [None, None]}
        if not xcol:
            p["time_grain_sqla"] = g
        if typ in ("line", "area"):
            p["seriesType"] = "line"; p["markerSize"] = 6; p["opacity"] = 0.2
        if typ == "scatter":
            p["markerSize"] = 6
        if typ == "bar":
            p["orientation"] = "vertical"; p["sort_series_type"] = "sum"
        if chart.get("stacked"):
            p["stack"] = "Stack"; qc["form_data"]["stack"] = "Stack"
        return viz, p, qc

    if typ == "bignum_trend":
        m = _sqlmetric(chart["metric"], sid, 0)
        qc = {"datasource": {"id": n, "type": "table"}, "force": False, "queries": [{
            "filters": [{"col": "__time", "op": "TEMPORAL_RANGE", "val": tr}],
            "extras": {"time_grain_sqla": g, "having": "", "where": ""}, "applied_time_extras": {},
            "columns": [{"timeGrain": g, "columnType": "BASE_AXIS", "sqlExpression": "__time",
                         "label": "__time", "expressionType": "SQL"}],
            "metrics": [m], "annotation_layers": [], "series_limit": 0, "order_desc": True,
            "url_params": {}, "custom_params": {}, "custom_form_data": {},
            "post_processing": [{"operation": "pivot", "options": {"index": ["__time"], "columns": [],
                                "aggregates": {_mlabel(m): {"operator": "mean"}}, "drop_missing_columns": True}},
                                {"operation": "flatten"}]}],
            "form_data": {**base, "viz_type": "big_number", "x_axis": "__time", "time_grain_sqla": g,
                          "metric": m, "adhoc_filters": afilt, "show_timestamp": True, "show_trend_line": True,
                          "start_y_axis_at_zero": True, "header_font_size": 0.4, "subheader_font_size": 0.15,
                          "y_axis_format": chart.get("format", "SMART_NUMBER"), "time_format": "smart_date"},
            "result_format": "json", "result_type": "full"}
        p = {**base, "viz_type": "big_number", "x_axis": "__time", "time_grain_sqla": g, "metric": m,
             "adhoc_filters": afilt, "show_timestamp": True, "show_trend_line": True, "start_y_axis_at_zero": True,
             "color_picker": {"r": 102, "g": 177, "b": 232, "a": 1}, "header_font_size": 0.4,
             "subheader_font_size": 0.15, "y_axis_format": chart.get("format", "SMART_NUMBER"),
             "time_format": "smart_date", "rolling_type": "None", "rolling_periods": 12, "min_periods": 8}
        return "big_number", p, qc

    if typ == "gauge":
        m = _sqlmetric(chart["metric"], sid, 0)
        gb = chart.get("groupby", [])
        qc = {"datasource": {"id": n, "type": "table"}, "force": False, "queries": [{
            "filters": [{"col": "__time", "op": "TEMPORAL_RANGE", "val": tr}],
            "extras": {"having": "", "where": ""}, "applied_time_extras": {}, "columns": gb,
            "metrics": [m], "annotation_layers": [], "row_limit": chart.get("row_limit", 10),
            "series_limit": 0, "order_desc": True, "url_params": {}, "custom_params": {}, "custom_form_data": {}}],
            "form_data": {**base, "viz_type": "gauge_chart", "groupby": gb, "metric": m, "adhoc_filters": afilt,
                          "row_limit": chart.get("row_limit", 10)},
            "result_format": "json", "result_type": "full"}
        p = {**base, "viz_type": "gauge_chart", "groupby": gb, "metric": m, "adhoc_filters": afilt,
             "row_limit": chart.get("row_limit", 10), "start_angle": 225, "end_angle": -45,
             "color_scheme": "acxColor", "font_size": 15, "number_format": chart.get("format", "SMART_NUMBER"),
             "value_formatter": "{value}", "show_pointer": True, "animation": True, "split_number": 10,
             "show_progress": True, "overlap": True}
        return "gauge_chart", p, qc

    if typ == "heatmap":
        m = _sqlmetric(chart["metric"], sid, 0)
        gbspec = chart["groupby"]
        ydim = gbspec[0] if isinstance(gbspec, list) else gbspec
        hm_rl = chart.get("row_limit", 10000)
        qc = {"datasource": {"id": n, "type": "table"}, "force": False, "queries": [{
            "filters": [{"col": "__time", "op": "TEMPORAL_RANGE", "val": tr}],
            "extras": {"time_grain_sqla": g, "having": "", "where": ""}, "applied_time_extras": {},
            "columns": [{"timeGrain": g, "columnType": "BASE_AXIS", "sqlExpression": "__time",
                         "label": "__time", "expressionType": "SQL"}, ydim],
            "metrics": [m], "orderby": [], "annotation_layers": [], "row_limit": hm_rl, "series_limit": 0,
            "order_desc": True, "url_params": {}, "custom_params": {}, "custom_form_data": {},
            "post_processing": [{"operation": "rank", "options": {"metric": _mlabel(m)}}]}],
            "form_data": {**base, "viz_type": "heatmap_v2", "x_axis": "__time", "time_grain_sqla": g,
                          "groupby": ydim, "metric": m, "adhoc_filters": afilt, "row_limit": hm_rl},
            "result_format": "json", "result_type": "full"}
        p = {**base, "viz_type": "heatmap_v2", "x_axis": "__time", "time_grain_sqla": g, "groupby": ydim,
             "metric": m, "adhoc_filters": afilt, "row_limit": hm_rl, "normalize_across": "heatmap",
             "legend_type": "continuous", "linear_color_scheme": "acxSequential", "xscale_interval": -1,
             "yscale_interval": -1, "left_margin": "auto", "bottom_margin": "auto", "value_bounds": [None, None],
             "y_axis_format": chart.get("format", "SMART_NUMBER"), "x_axis_time_format": "smart_date",
             "show_legend": True, "show_percentage": True}
        return "heatmap_v2", p, qc

    if typ == "pie":
        m = _sqlmetric(chart["metric"], sid, 0)
        gb = chart["groupby"]
        dfs = [_dfilter(c, v, sid) for c, v in _norm_filters(chart)]
        qc = {"datasource": {"id": n, "type": "table"}, "force": False, "queries": [{
            "extras": {"where": "", "having": ""}, "columns": gb, "filters": dfs, "metrics": [m],
            "orderby": [[m, False]], "row_limit": chart.get("row_limit", 15), "order_desc": True,
            "time_range": tr, "url_params": {}, "granularity": "__time", "custom_params": {},
            "custom_form_data": {}, "timeseries_limit": 0, "annotation_layers": [], "applied_time_extras": {}}],
            "form_data": {**base, "viz_type": "pie", "metric": m, "groupby": gb,
                          "row_limit": chart.get("row_limit", 15), "time_range": tr, "granularity": "__time"},
            "result_type": "full", "result_format": "json"}
        p = {**base, "donut": True, "metric": m, "schema": "druid", "groupby": gb, "viz_type": "pie",
             "row_limit": chart.get("row_limit", 15), "label_line": True, "label_type": "key_percent",
             "legendType": "scroll", "time_range": tr, "date_format": "smart_date", "granularity": "__time",
             "innerRadius": 42, "outerRadius": 58, "show_labels": True, "color_scheme": "acxColor",
             "adhoc_filters": dfs, "database_name": "Apache Druid", "number_format": "SMART_NUMBER",
             "labels_outside": True, "sort_by_metric": True, "granularity_sqla": "__time",
             "legendOrientation": "top", "show_legend_label": True, "show_labels_threshold": ""}
        return "pie", p, qc

    if typ == "table":
        ms = [_sqlmetric(m, sid, i) for i, m in enumerate(chart["metrics"])]
        pm = [_sqlmetric(m, sid, 100 + i) for i, m in enumerate(chart.get("percent_of_total", []))]
        gb = chart["groupby"]
        # per-column number formats: {"<metric label>": "<d3 format>"} -> Superset column_config
        column_config = {lbl: {"d3NumberFormat": fmt}
                         for lbl, fmt in chart.get("column_formats", {}).items()}
        qc = {"datasource": {"id": n, "type": "table"}, "force": False, "queries": [{
            "filters": [{"col": "__time", "op": "TEMPORAL_RANGE", "val": tr}],
            "extras": {"having": "", "where": ""}, "applied_time_extras": {}, "columns": gb, "metrics": ms,
            "orderby": [[ms[0], False]], "annotation_layers": [], "row_limit": chart.get("row_limit", 50),
            "series_limit": 0, "order_desc": True, "url_params": {}, "custom_params": {},
            "custom_form_data": {}, "post_processing": []}],
            "form_data": {**base, "viz_type": "table", "query_mode": "aggregate", "groupby": gb,
                          "metrics": ms, "percent_metrics": pm, "adhoc_filters": afilt, "column_config": column_config,
                          "row_limit": chart.get("row_limit", 50), "server_pagination": True, "order_desc": True},
            "result_format": "json", "result_type": "full"}
        p = {**base, "viz_type": "table", "query_mode": "aggregate", "groupby": gb, "all_columns": [],
             "percent_metrics": pm, "metrics": ms, "adhoc_filters": afilt, "order_by_cols": [],
             "column_config": column_config,
             "row_limit": chart.get("row_limit", 50), "server_pagination": True, "order_desc": True,
             "table_timestamp_format": "smart_date", "color_scheme": "acxColor"}
        return "table", p, qc

    if typ == "funnel":
        m = _sqlmetric(chart["metric"], sid, 0)
        gb = chart.get("groupby", [])
        qc = {"datasource": {"id": n, "type": "table"}, "force": False, "queries": [{
            "filters": [{"col": "__time", "op": "TEMPORAL_RANGE", "val": tr}],
            "extras": {"having": "", "where": ""}, "applied_time_extras": {}, "columns": gb,
            "metrics": [m], "annotation_layers": [], "row_limit": chart.get("row_limit", 10),
            "series_limit": 0, "order_desc": True, "url_params": {}, "custom_params": {}, "custom_form_data": {}}],
            "form_data": {**base, "viz_type": "funnel", "groupby": gb, "metric": m, "adhoc_filters": afilt,
                          "row_limit": chart.get("row_limit", 10)},
            "result_format": "json", "result_type": "full"}
        p = {**base, "viz_type": "funnel", "groupby": gb, "metric": m, "adhoc_filters": afilt,
             "row_limit": chart.get("row_limit", 10), "sort_by_metric": True, "percent_calculation_type": "total",
             "color_scheme": "acxColor", "show_legend": True, "legendOrientation": "top", "tooltip_label_type": 5,
             "number_format": chart.get("format", "SMART_NUMBER"), "show_labels": True, "show_tooltip_labels": True}
        return "funnel", p, qc

    if typ == "pivot":
        ms = [_sqlmetric(m, sid, i) for i, m in enumerate(chart["metrics"])]
        rows = chart.get("rows", [])
        cols = chart.get("columns", [])
        qc = {"datasource": {"id": n, "type": "table"}, "force": False, "queries": [{
            "filters": [{"col": "__time", "op": "TEMPORAL_RANGE", "val": tr}],
            "extras": {"having": "", "where": ""}, "applied_time_extras": {}, "columns": cols + rows,
            "metrics": ms, "annotation_layers": [], "row_limit": chart.get("row_limit", 10000),
            "series_limit": 0, "order_desc": True, "url_params": {}, "custom_params": {}, "custom_form_data": {}}],
            "form_data": {**base, "viz_type": "pivot_table_v2", "groupbyColumns": cols, "groupbyRows": rows,
                          "metrics": ms, "metricsLayout": "COLUMNS", "adhoc_filters": afilt,
                          "row_limit": chart.get("row_limit", 10000), "aggregateFunction": "Sum"},
            "result_format": "json", "result_type": "full"}
        p = {**base, "viz_type": "pivot_table_v2", "groupbyColumns": cols, "groupbyRows": rows,
             "temporal_columns_lookup": {"__time": True}, "metrics": ms, "metricsLayout": "COLUMNS",
             "adhoc_filters": afilt, "row_limit": chart.get("row_limit", 10000), "order_desc": True,
             "aggregateFunction": "Sum", "valueFormat": chart.get("format", "SMART_NUMBER"),
             "date_format": "smart_date", "rowOrder": "key_a_to_z", "colOrder": "key_a_to_z", "allow_render_html": True}
        return "pivot_table_v2", p, qc

    if typ == "mixed":
        ms = [_sqlmetric(m, sid, i) for i, m in enumerate(chart["metrics"])]
        msb = [_sqlmetric(m, sid, 50 + i) for i, m in enumerate(chart.get("metrics_b", []))]
        gb = chart.get("groupby", [])
        gbb = chart.get("groupby_b", [])

        def _q(metrics, groupby, drop):
            return {"filters": [{"col": "__time", "op": "TEMPORAL_RANGE", "val": tr}],
                    "extras": {"time_grain_sqla": g, "having": "", "where": ""}, "applied_time_extras": {},
                    "columns": [{"columnType": "BASE_AXIS", "sqlExpression": "__time", "label": "__time",
                                 "expressionType": "SQL"}] + list(groupby),
                    "metrics": metrics, "orderby": ([[metrics[0], False]] if metrics else []),
                    "annotation_layers": [], "row_limit": 10000, "series_columns": groupby, "series_limit": 0,
                    "order_desc": True, "url_params": {}, "custom_params": {}, "custom_form_data": {}, "time_offsets": [],
                    "post_processing": [{"operation": "pivot", "options": {"index": ["__time"], "columns": groupby,
                                        "aggregates": {_mlabel(m): {"operator": "mean"} for m in metrics},
                                        "drop_missing_columns": drop}}, {"operation": "flatten"}]}
        qc = {"datasource": {"id": n, "type": "table"}, "force": False,
              "queries": [_q(ms, gb, False), _q(msb, gbb, True)],
              "form_data": {**base, "viz_type": "mixed_timeseries", "x_axis": "__time", "metrics": ms,
                            "groupby": gb, "metrics_b": msb, "groupby_b": gbb, "adhoc_filters": afilt,
                            "adhoc_filters_b": afilt},
              "result_format": "json", "result_type": "full"}
        p = {**base, "viz_type": "mixed_timeseries", "x_axis": "__time", "metrics": ms, "groupby": gb,
             "adhoc_filters": afilt, "order_desc": True, "row_limit": 10000, "truncate_metric": True,
             "comparison_type": "values", "metrics_b": msb, "groupby_b": gbb, "adhoc_filters_b": afilt,
             "order_desc_b": True, "row_limit_b": 10000, "truncate_metric_b": True, "comparison_type_b": "values",
             "annotation_layers": [], "color_scheme": "acxColor", "seriesType": "bar", "opacity": 0.6,
             "markerSize": 6, "seriesTypeB": "line", "opacityB": 0.2, "markerSizeB": 6, "show_legend": True,
             "legendType": "scroll", "legendOrientation": "top", "x_axis_time_format": "smart_date",
             "rich_tooltip": True, "showTooltipTotal": True, "y_axis_format": chart.get("format", "SMART_NUMBER"),
             "y_axis_format_secondary": chart.get("format_b", "SMART_NUMBER"), "y_axis_bounds": [None, None],
             "y_axis_bounds_secondary": [None, None]}
        return "mixed_timeseries", p, qc

    if typ == "tree":
        m = _sqlmetric(chart["metric"], sid, 0)
        idc, parc = chart["id"], chart["parent"]
        namec = chart.get("name", idc)
        qc = {"datasource": {"id": n, "type": "table"}, "force": False, "queries": [{
            "filters": [{"col": "__time", "op": "TEMPORAL_RANGE", "val": tr}],
            "extras": {"having": "", "where": ""}, "applied_time_extras": {}, "columns": [idc, parc, namec],
            "metrics": [m], "annotation_layers": [], "row_limit": chart.get("row_limit", 100),
            "series_limit": 0, "order_desc": True, "url_params": {}, "custom_params": {}, "custom_form_data": {}}],
            "form_data": {**base, "viz_type": "tree_chart", "id": idc, "parent": parc, "name": namec,
                          "metric": m, "adhoc_filters": afilt, "row_limit": chart.get("row_limit", 100)},
            "result_format": "json", "result_type": "full"}
        p = {**base, "viz_type": "tree_chart", "id": idc, "parent": parc, "name": namec, "metric": m,
             "adhoc_filters": afilt, "row_limit": chart.get("row_limit", 100), "layout": "orthogonal",
             "orient": "LR", "node_label_position": "left", "child_label_position": "bottom",
             "emphasis": "descendant", "symbol": "emptyCircle", "symbolSize": 7, "roam": True}
        return "tree_chart", p, qc

    if typ == "bubble":
        ent = chart["entity"]
        mx = _sqlmetric(chart["x"], sid, 0)
        my = _sqlmetric(chart["y"], sid, 1)
        msz = _sqlmetric(chart["size"], sid, 2)
        rl = chart.get("row_limit", 10000)
        qc = {"datasource": {"id": n, "type": "table"}, "force": False, "queries": [{
            "filters": [{"col": "__time", "op": "TEMPORAL_RANGE", "val": tr}],
            "extras": {"having": "", "where": ""}, "applied_time_extras": {}, "columns": [ent],
            "metrics": [mx, my, msz], "annotation_layers": [], "row_limit": rl, "series_limit": 0,
            "order_desc": True, "url_params": {}, "custom_params": {}, "custom_form_data": {}}],
            "form_data": {**base, "viz_type": "bubble_v2", "entity": ent, "x": mx, "y": my, "size": msz,
                          "adhoc_filters": afilt, "row_limit": rl},
            "result_format": "json", "result_type": "full"}
        p = {**base, "viz_type": "bubble_v2", "entity": ent, "x": mx, "y": my, "size": msz,
             "adhoc_filters": afilt, "order_desc": True, "row_limit": rl, "color_scheme": "acxColor",
             "show_legend": True, "legendType": "scroll", "legendOrientation": "top", "max_bubble_size": "25",
             "tooltipSizeFormat": "SMART_NUMBER", "opacity": 0.6, "x_axis_title_margin": 30,
             "xAxisFormat": chart.get("format", "SMART_NUMBER"), "y_axis_title_margin": 30,
             "y_axis_format": chart.get("format", "SMART_NUMBER"), "truncateXAxis": True,
             "y_axis_bounds": [None, None]}
        return "bubble_v2", p, qc

    raise ValueError(f"unknown chart type: {typ!r}")


def _dashboard_yaml(title, tenant, rows, charts_meta):
    L = ["dashboard_title: " + json.dumps(title), "description: null", "css: null", "slug: null",
         "certified_by: null", "certification_details: null", "published: true",
         f"uuid: {_stable_uuid('dashboard', title)}"]
    if tenant:
        L += ["metadata:", "  tenant_ids:", f"  - {tenant}"]
    else:
        L += ["metadata: {}"]
    L += ["position:",
         "  DASHBOARD_VERSION_KEY: v2", "  ROOT_ID:", "    children:", "    - GRID_ID",
         "    id: ROOT_ID", "    type: ROOT", "  GRID_ID:", "    children:"]
    for ri in range(len(rows)):
        L.append(f"    - ROW-{ri}")
    L += ["    id: GRID_ID", "    parents:", "    - ROOT_ID", "    type: GRID",
          "  HEADER_ID:", "    id: HEADER_ID", "    meta:", f"      text: {json.dumps(title)}", "    type: HEADER"]
    for ri, row in enumerate(rows):
        L += [f"  ROW-{ri}:", "    children:"]
        for ci in range(len(row)):
            L.append(f"    - CHART-{ri}-{ci}")
        L += [f"    id: ROW-{ri}", "    meta:", "      background: BACKGROUND_TRANSPARENT",
              "    type: ROW", "    parents:", "    - ROOT_ID", "    - GRID_ID"]
        for ci, ch in enumerate(row):
            meta = charts_meta[(ri, ci)]
            L += [f"  CHART-{ri}-{ci}:", "    children: []", f"    id: CHART-{ri}-{ci}", "    meta:",
                  f"      chartId: {meta['sid']}",
                  f"      height: {HEIGHT.get(ch['type'], DEFAULT_HEIGHT)}",
                  f"      sliceName: {json.dumps(ch['title'])}", f"      uuid: {meta['uuid']}",
                  f"      width: {ch.get('width', 4)}", "    type: CHART", "    parents:",
                  "    - ROOT_ID", "    - GRID_ID", f"    - ROW-{ri}"]
    L.append("version: 1.0.0")
    return "\n".join(L) + "\n"


# Valid Data Studio relative-range literals. Other "Last ..." (e.g. "Last hour")
# error with DATASTUDIO-20000; sub-day needs a Custom explicit range instead.
_VALID_TIME_RANGES = {
    "Last day", "Last week", "Last month", "Last quarter",
    "previous calendar week", "previous calendar month", "No filter",
}


def _bad_time_range(tr):
    """True if tr is an unsupported relative-range literal."""
    if not isinstance(tr, str) or not tr:
        return False
    t = tr.strip()
    if t in _VALID_TIME_RANGES:
        return False
    # reject unsupported "Last ..."/"previous ..." before the digit check below
    if t.lower().startswith(("last ", "previous ")):
        return True
    if " : " in t or "T" in t or any(c.isdigit() for c in t):  # explicit/custom range
        return False
    return False


# Chart type registry — single source of truth for valid types and their required/optional keys.
# 'metric' = ONE metric (string, or {"sql","label"}); 'metrics' = a LIST of them. That singular
# vs plural split is the most common mistake, so validate_spec enforces it per type.
CHART_TYPES = {
    "bignum":       {"required": ["metric"],                 "optional": ["format"],
                     "desc": "single KPI number"},
    "bignum_trend": {"required": ["metric"],                 "optional": ["format", "grain"],
                     "desc": "KPI with sparkline (latest bucket value + trend line)"},
    "line":         {"required": ["metrics"],                "optional": ["x", "groupby", "stacked", "format", "grain"],
                     "desc": "line over time (or over dimension 'x')"},
    "bar":          {"required": ["metrics"],                "optional": ["x", "groupby", "stacked", "format", "grain"],
                     "desc": "bar over time (or over dimension 'x')"},
    "area":         {"required": ["metrics"],                "optional": ["x", "groupby", "stacked", "format", "grain"],
                     "desc": "stacked/area over time"},
    "scatter":      {"required": ["metrics"],                "optional": ["x", "groupby", "format", "grain"],
                     "desc": "scatter (categorical x-axis)"},
    "pie":          {"required": ["metric", "groupby"],      "optional": ["filter", "row_limit"],
                     "desc": "share of one metric across dim(s)"},
    "table":        {"required": ["metrics", "groupby"],     "optional": ["percent_of_total", "column_formats", "row_limit"],
                     "desc": "aggregate table; sorts by the first metric descending"},
    "gauge":        {"required": ["metric"],                 "optional": ["groupby", "format", "row_limit"],
                     "desc": "gauge snapshot of one metric"},
    "heatmap":      {"required": ["metric", "groupby"],      "optional": ["format", "grain", "row_limit"],
                     "desc": "time x (first groupby dim) heatmap"},
    "funnel":       {"required": ["metric"],                 "optional": ["groupby", "format", "row_limit"],
                     "desc": "funnel of one metric across a stage dim"},
    "pivot":        {"required": ["metrics"],                "optional": ["rows", "columns", "format", "row_limit"],
                     "desc": "pivot table (rows x columns of dims, metrics in cells)"},
    "mixed":        {"required": ["metrics"],                "optional": ["metrics_b", "groupby", "groupby_b", "format", "format_b"],
                     "desc": "bars (metrics) + line (metrics_b) on one time axis"},
    "tree":         {"required": ["metric", "id", "parent"], "optional": ["name", "row_limit"],
                     "desc": "tree/hierarchy (id -> parent), sized by metric"},
    "bubble":       {"required": ["entity", "x", "y", "size"], "optional": ["row_limit"],
                     "desc": "bubble; entity is a DIM, x/y/size are METRICS"},
}
VALID_TYPES = tuple(CHART_TYPES)


def validate_spec(spec, catalog):
    """Return a list of problem strings ([] == clean). Checks datasets, saved-metric & dim names."""
    by_name = {d["name"]: d for d in catalog["datasets"]}
    problems = []
    if not spec.get("title"):
        problems.append("spec.title is required (use a generic, non-tenant name)")
    if _bad_time_range(spec.get("time_range")):
        problems.append(f"spec.time_range {spec['time_range']!r} is not a valid Data Studio range "
                        "(use Last day/week/month/quarter/year, or an explicit range)")
    rows = spec.get("rows") or []
    if not rows:
        problems.append("spec.rows is empty")
    for ri, row in enumerate(rows):
        for ci, ch in enumerate(row):
            loc = f"rows[{ri}][{ci}] '{ch.get('title','?')}'"
            if _bad_time_range(ch.get("time_range")):
                problems.append(f"{loc}: time_range {ch['time_range']!r} is not a valid Data Studio "
                                "range (use Last day/week/month/quarter/year, or an explicit range)")
            ds = by_name.get(ch.get("dataset"))
            if not ds:
                problems.append(f"{loc}: unknown dataset {ch.get('dataset')!r}")
                continue
            mset, dset = set(ds["metrics"]), set(ds["dims"])
            is_bubble = ch.get("type") == "bubble"
            metrics = (ch.get("metrics", []) + ([ch["metric"]] if ch.get("metric") else [])
                       + ch.get("percent_of_total", []) + ch.get("metrics_b", []))
            if is_bubble:  # bubble x/y/size are METRICS, not dims
                metrics += [ch[k] for k in ("x", "y", "size") if ch.get(k)]
            for m in metrics:
                if isinstance(m, str) and m not in mset:
                    problems.append(f"{loc}: metric {m!r} not in {ds['name']} (have: {sorted(mset)[:6]}...)")
            dims_used = (list(ch.get("groupby", [])) + list(ch.get("groupby_b", []))
                         + list(ch.get("rows", [])) + list(ch.get("columns", [])))
            for k in ("id", "parent", "name"):
                if ch.get(k):
                    dims_used.append(ch[k])
            if is_bubble:
                if ch.get("entity"):
                    dims_used.append(ch["entity"])
            elif ch.get("x"):  # timeseries dimension x-axis
                dims_used.append(ch["x"])
            for col in dims_used:
                if col not in dset:
                    problems.append(f"{loc}: dimension {col!r} not a dim of {ds['name']}")
            for col, _ in _norm_filters(ch):
                if col not in dset:
                    problems.append(f"{loc}: filter col {col!r} not a dim of {ds['name']}")
            typ = ch.get("type")
            if typ not in CHART_TYPES:
                problems.append(f"{loc}: bad type {typ!r} (valid: {', '.join(VALID_TYPES)})")
                continue
            # per-type required keys — so validate_spec agrees with build_dashboard
            # (build accesses these directly and would otherwise raise a raw KeyError).
            for key in CHART_TYPES[typ]["required"]:
                val = ch.get(key)
                empty = val is None or (isinstance(val, (list, dict, str)) and len(val) == 0)
                if empty:
                    hint = ""
                    if key == "metric" and ch.get("metrics"):
                        hint = " — this type wants 'metric' (a single metric), not 'metrics' (a list)"
                    elif key == "metrics" and ch.get("metric"):
                        hint = " — this type wants 'metrics' (a list), not 'metric' (a single metric)"
                    problems.append(f"{loc}: type {typ!r} requires {key!r}{hint} "
                                    f"(required: {CHART_TYPES[typ]['required']})")
    return problems


def build_dashboard(spec, catalog, out_path, sid_base=900000):
    """Build a Superset import bundle .zip at out_path. Returns dict summary."""
    problems = validate_spec(spec, catalog)
    if problems:
        raise ValueError("spec validation failed:\n  - " + "\n  - ".join(problems))
    by_name = {d["name"]: d for d in catalog["datasets"]}
    title = spec["title"]
    tenant = spec.get("tenant_id")  # optional — omitted from bundle if absent (import rescopes to target EC)
    default_tr = spec.get("time_range", "Last week")
    default_grain = spec.get("grain")  # optional board-level time grain (charts can override)
    rows = spec["rows"]

    work = os.path.join(os.path.dirname(out_path) or ".", f".build_{uuid.uuid4().hex[:8]}")
    root = os.path.join(work, "export")
    os.makedirs(os.path.join(root, "charts"))
    os.makedirs(os.path.join(root, "dashboards"))

    dash_id = sid_base + 1
    charts_meta = {}
    sid = sid_base + 10
    for ri, row in enumerate(rows):
        for ci, ch in enumerate(row):
            ch.setdefault("title", f"chart-{ri}-{ci}")
            ch["_time_range"] = default_tr
            if default_grain:
                ch["_grain"] = default_grain
            ds = by_name[ch["dataset"]]
            sid += 1
            ch_uuid = _stable_uuid("chart", title, ri, ci, ch["title"])
            viz, params, qc = _build_chart(ch, ds, tenant, dash_id, sid)
            yaml_txt = _chart_yaml(viz, params, qc, sid, ch_uuid, ds["dataset_uuid"])
            fn = (ch["title"].replace(" ", "_").replace("/", "_").replace("(", "")
                  .replace(")", "").replace("%", "pct") + f"_{sid}.yaml")
            with open(os.path.join(root, "charts", fn), "w") as f:
                f.write(yaml_txt)
            charts_meta[(ri, ci)] = {"sid": sid, "uuid": ch_uuid}

    with open(os.path.join(root, "dashboards", f"{title.replace(' ', '_')}_{dash_id}.yaml"), "w") as f:
        f.write(_dashboard_yaml(title, tenant, rows, charts_meta))
    with open(os.path.join(root, "metadata.yaml"), "w") as f:
        f.write("version: 1.0.0\ntype: Dashboard\ndeployment: ALTO\n")

    if os.path.exists(out_path):
        os.remove(out_path)
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as z:
        for dp, _, fs in os.walk(root):
            for f in fs:
                full = os.path.join(dp, f)
                z.write(full, os.path.relpath(full, work))
    shutil.rmtree(work, ignore_errors=True)
    return {"output": out_path, "title": title, "charts": sid - (sid_base + 10),
            "datasets": sorted({ch["dataset"] for row in rows for ch in row})}


if __name__ == "__main__":
    import sys
    cat = json.load(open(os.path.join(os.path.dirname(__file__), "catalog.json")))
    spec = json.load(open(sys.argv[1]))
    out = sys.argv[2] if len(sys.argv) > 2 else "dashboard_IMPORT.zip"
    print(build_dashboard(spec, cat, out))
