#!/usr/bin/env python3
"""Generate claude-code-dashboard.yaml (Grafana dashboard ConfigMap) — see docs/claude-code-telemetry.md.

Claude Code exports *per-session cumulative* counters (one series per
session_id, counter starts at the first turn's value and the series ends when
the session exits).  Prometheus increase()/rate() mishandle that shape: a series
whose first sample already carries value v contributes ~0 if it stays flat, and
single-sample series contribute nothing.  So every "how much in window W"
query here uses the exact per-session form:

    last_in_window - (last_before_window if the session already existed else 0)

expressed as   (A - (B and A)) or (A * 1)
   A = last_over_time(m[W])              last value of each session inside W
   B = last_over_time(m[W] offset W)     last value of each session before W
   B and A   -> only sessions that were alive at the start of W
   or (A*1)  -> sessions born inside W contribute their full value
"""
import json, sys

PROM = {"type": "prometheus", "uid": "prometheus-cluster"}
LOKI = {"type": "loki", "uid": "loki-cluster"}
HOST = 'host_name=~"$host"'
LHOST = 'attributes_host_name=~"$host"'


def inc(metric, win, by=None):
    a = f"last_over_time({metric}[{win}])"
    b = f"last_over_time({metric}[{win}] offset {win})"
    body = f"(({a} - ({b} and {a})) or ({a} * 1))"
    return f"sum by ({by}) {body}" if by else f"sum{body}"


def rng(metric, by=None):
    return inc(metric, "$__range", by)


def bucket(metric, by=None):
    return inc(metric, "$__interval", by)


def m(name, extra=""):
    sel = HOST + (", " + extra if extra else "")
    return f"{name}{{{sel}}}"


_id = [0]


def nid():
    _id[0] += 1
    return _id[0]


def stat(title, expr, unit, x, y, w, h=4, ds=PROM, decimals=None, color="text", desc=None, loki=False):
    tgt = {"refId": "A", "expr": expr}
    if loki:
        tgt.update({"queryType": "instant"})
    else:
        tgt.update({"instant": True, "range": False})
    p = {
        "id": nid(), "type": "stat", "title": title, "datasource": ds,
        "gridPos": {"h": h, "w": w, "x": x, "y": y},
        "targets": [tgt],
        "options": {"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
                    "graphMode": "none", "colorMode": "value", "textMode": "value", "wideLayout": True},
        "fieldConfig": {"defaults": {"unit": unit, "noValue": "0",
                                     "color": {"mode": "fixed", "fixedColor": color}}, "overrides": []},
    }
    if decimals is not None:
        p["fieldConfig"]["defaults"]["decimals"] = decimals
    if desc:
        p["description"] = desc
    return p


def timeseries(title, targets, unit, x, y, w, h=8, ds=PROM, stack=False, bars=False, interval="1h",
               desc=None, fill=None, decimals=None, legend_calcs=("sum",), min0=True):
    style = {"drawStyle": "bars" if bars else "line", "lineWidth": 1, "fillOpacity": fill if fill is not None else (80 if bars else 10),
             "pointSize": 4, "showPoints": "never", "spanNulls": False, "gradientMode": "none",
             "stacking": {"mode": "normal" if stack else "none", "group": "A"}}
    if bars:
        style["barAlignment"] = -1  # bar covers the interval that ENDS at the point
    p = {
        "id": nid(), "type": "timeseries", "title": title, "datasource": ds,
        "gridPos": {"h": h, "w": w, "x": x, "y": y},
        "targets": targets,
        "options": {"legend": {"displayMode": "table", "placement": "right", "calcs": list(legend_calcs), "showLegend": True},
                    "tooltip": {"mode": "multi", "sort": "desc"}},
        "fieldConfig": {"defaults": {"unit": unit, "custom": style, "color": {"mode": "palette-classic"}},
                        "overrides": []},
    }
    if min0:
        p["fieldConfig"]["defaults"]["min"] = 0
    if decimals is not None:
        p["fieldConfig"]["defaults"]["decimals"] = decimals
    if interval:
        p["interval"] = interval
    if desc:
        p["description"] = desc
    return p


def ptarget(expr, legend, ref="A", instant=False, fmt=None):
    t = {"refId": ref, "expr": expr, "legendFormat": legend}
    if instant:
        t.update({"instant": True, "range": False})
    if fmt:
        t["format"] = fmt
    return t


def ltarget(expr, legend, ref="A", instant=False):
    t = {"refId": ref, "expr": expr, "legendFormat": legend, "queryType": "instant" if instant else "range"}
    return t


def piechart(title, expr, legend, unit, x, y, w, h=8, desc=None):
    return {
        "id": nid(), "type": "piechart", "title": title, "datasource": PROM,
        "gridPos": {"h": h, "w": w, "x": x, "y": y},
        "targets": [ptarget(expr, legend, instant=True)],
        "options": {"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
                    "pieType": "donut", "displayLabels": ["percent"],
                    "legend": {"displayMode": "table", "placement": "right", "values": ["value", "percent"], "showLegend": True},
                    "tooltip": {"mode": "single"}},
        "fieldConfig": {"defaults": {"unit": unit, "decimals": 2, "color": {"mode": "palette-classic"}}, "overrides": []},
        **({"description": desc} if desc else {}),
    }


def bargauge(title, targets, unit, x, y, w, h=8, ds=PROM, desc=None, decimals=2, max_items=None):
    p = {
        "id": nid(), "type": "bargauge", "title": title, "datasource": ds,
        "gridPos": {"h": h, "w": w, "x": x, "y": y},
        "targets": targets,
        "options": {"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
                    "orientation": "horizontal", "displayMode": "lcd", "valueMode": "color",
                    "showUnfilled": True, "namePlacement": "top", "sizing": "auto", "minVizHeight": 16, "minVizWidth": 8},
        "fieldConfig": {"defaults": {"unit": unit, "decimals": decimals, "min": 0,
                                     "color": {"mode": "continuous-BlPu"}}, "overrides": []},
        "transformations": [{"id": "sortBy", "options": {"sort": [{"field": "Value", "desc": True}]}}],
    }
    if max_items:
        p["transformations"].append({"id": "limit", "options": {"limitField": max_items}})
    if desc:
        p["description"] = desc
    return p


panels = []

# ---------------------------------------------------------------- row 0: headline stats
y = 0
cost_total = rng(m("claude_code_cost_usage"))
tok_total = rng(m("claude_code_token_usage"))
sessions_total = f'count(count by (session_id) (last_over_time({m("claude_code_session_count")}[$__range])))'
active_total = rng(m("claude_code_active_time_total"))
api_reqs = f'sum(count_over_time({{job="claude-code"}} | json | body="claude_code.api_request" | {LHOST} [$__range]))'
_cr = m("claude_code_token_usage", 'type="cacheRead"')
_ci = m("claude_code_token_usage", 'type=~"input|cacheRead|cacheCreation"')
cache_ratio = f"{rng(_cr)} / {rng(_ci)}"
lines_added = rng(m("claude_code_lines_of_code_count", 'type="added"'))
lines_removed = rng(m("claude_code_lines_of_code_count", 'type="removed"'))

panels += [
    stat("Cost", cost_total, "currencyUSD", 0, y, 4, decimals=2, color="orange",
         desc="Estimated API cost over the dashboard time range (sum over all sessions, exact per-session accounting)."),
    stat("Tokens", tok_total, "short", 4, y, 4, decimals=1, color="blue",
         desc="All token types (input + output + cache read + cache creation)."),
    stat("Sessions", sessions_total, "short", 8, y, 3, decimals=0, color="purple",
         desc="Distinct session_id values that reported a session.count sample in range."),
    stat("API requests", api_reqs, "short", 11, y, 3, ds=LOKI, decimals=0, color="green", loki=True,
         desc="claude_code.api_request log events (from Loki)."),
    stat("Active time", active_total, "s", 14, y, 3, color="text",
         desc="claude_code.active_time.total — wall time the CLI was actively working."),
    stat("Cache hit", cache_ratio, "percentunit", 17, y, 3, decimals=1, color="green",
         desc="cacheRead / (input + cacheRead + cacheCreation). Higher = cheaper turns."),
    stat("Lines +", lines_added, "short", 20, y, 2, decimals=0, color="green"),
    stat("Lines −", lines_removed, "short", 22, y, 2, decimals=0, color="red"),
]

# ---------------------------------------------------------------- row 1: cost + tokens over time
y = 4
panels += [
    timeseries("Cost per interval by model", [ptarget(bucket(m("claude_code_cost_usage"), "model"), "{{model}}")],
               "currencyUSD", 0, y, 12, stack=True, bars=True, decimals=2,
               desc="USD spent in each bucket (≥1h), split by model. Bars are exact per-session deltas, not rate() extrapolations."),
    timeseries("Tokens per interval by type", [ptarget(bucket(m("claude_code_token_usage"), "type"), "{{type}}")],
               "short", 12, y, 12, stack=True, bars=True,
               desc="input / output / cacheRead / cacheCreation tokens per bucket (≥1h)."),
]

# ---------------------------------------------------------------- row 2: breakdowns
y = 12
top_sessions = f'topk(10, {rng(m("claude_code_cost_usage"), "session_id, host_name")})'
panels += [
    piechart("Cost by model", rng(m("claude_code_cost_usage"), "model"), "{{model}}", "currencyUSD", 0, y, 8),
    bargauge("Cost by host", [ptarget(rng(m("claude_code_cost_usage"), "host_name"), "{{host_name}}", instant=True)],
             "currencyUSD", 8, y, 8, desc="Which machine the spend came from (host.name resource attribute)."),
    bargauge("Top sessions by cost", [ptarget(top_sessions, "{{host_name}} · {{session_id}}", instant=True)],
             "currencyUSD", 16, y, 8, desc="Ten most expensive sessions in range. Paste the session_id into `claude --resume` to find it."),
]

# ---------------------------------------------------------------- row 3: API behaviour from Loki events
y = 20
api_base = f'{{job="claude-code"}} | json | body="claude_code.api_request" | {LHOST}'
panels += [
    timeseries("API latency by model (p50 / p95)", [
        ltarget(f'quantile_over_time(0.50, {api_base} | unwrap attributes_duration_ms [$__auto]) by (attributes_model)', "p50 {{attributes_model}}", "A"),
        ltarget(f'quantile_over_time(0.95, {api_base} | unwrap attributes_duration_ms [$__auto]) by (attributes_model)', "p95 {{attributes_model}}", "B"),
    ], "ms", 0, y, 12, ds=LOKI, interval=None, legend_calcs=("mean", "max"),
        desc="Round-trip duration of each Anthropic API request (duration_ms in api_request events)."),
    timeseries("Time to first token by model (p50 / p95)", [
        ltarget(f'quantile_over_time(0.50, {api_base} | unwrap attributes_ttft_ms [$__auto]) by (attributes_model)', "p50 {{attributes_model}}", "A"),
        ltarget(f'quantile_over_time(0.95, {api_base} | unwrap attributes_ttft_ms [$__auto]) by (attributes_model)', "p95 {{attributes_model}}", "B"),
    ], "ms", 12, y, 12, ds=LOKI, interval=None, legend_calcs=("mean", "max"),
        desc="ttft_ms from api_request events — how long before streaming starts."),
]

# ---------------------------------------------------------------- row 4: tools
y = 28
tool_base = f'{{job="claude-code"}} | json | body="claude_code.tool_result" | {LHOST}'
panels += [
    bargauge("Tool calls by tool", [ltarget(f'topk(15, sum by (attributes_tool_name) (count_over_time({tool_base} [$__range])))', "{{attributes_tool_name}}", instant=True)],
             "short", 0, y, 8, ds=LOKI, decimals=0, desc="tool_result events grouped by tool_name (top 15)."),
    bargauge("Tool failures by tool", [ltarget(f'sum by (attributes_tool_name) (count_over_time({tool_base} | attributes_success="false" [$__range]))', "{{attributes_tool_name}}", instant=True)],
             "short", 8, y, 8, ds=LOKI, decimals=0, desc="tool_result events with success=false."),
    bargauge("Tool duration p95 by tool", [ltarget(f'quantile_over_time(0.95, {tool_base} | unwrap attributes_duration_ms [$__range]) by (attributes_tool_name)', "{{attributes_tool_name}}", instant=True)],
             "ms", 16, y, 8, ds=LOKI, decimals=0, desc="95th percentile tool execution time over the range."),
]

# ---------------------------------------------------------------- row 5: productivity + activity
y = 36
panels += [
    timeseries("Lines of code per interval", [
        ptarget(bucket(m("claude_code_lines_of_code_count", 'type="added"')), "added", "A"),
        ptarget(bucket(m("claude_code_lines_of_code_count", 'type="removed"')), "removed", "B"),
    ], "short", 0, y, 8, bars=True, stack=False, decimals=0,
        desc="Lines Claude added/removed via Edit/Write tools (claude_code.lines_of_code.count)."),
    timeseries("Commits & pull requests per interval", [
        ptarget(bucket(m("claude_code_commit_count")), "commits", "A"),
        ptarget(bucket(m("claude_code_pull_request_count")), "pull requests", "B"),
    ], "short", 8, y, 8, bars=True, decimals=0),
    timeseries("Active sessions per interval", [
        ptarget(f'count(count by (session_id) (last_over_time({m("claude_code_session_count")}[$__interval])))', "sessions", "A"),
    ], "short", 16, y, 8, bars=True, decimals=0,
        desc="Sessions that reported at least one sample inside each bucket."),
]

# ---------------------------------------------------------------- row 6: errors log
y = 44
err_q = (f'{{job="claude-code"}} | json | (body="claude_code.api_error" or (body="claude_code.tool_result" and attributes_success="false")) | {LHOST} '
         f'| line_format "{{{{.body}}}} model={{{{.attributes_model}}}} tool={{{{.attributes_tool_name}}}} status={{{{.attributes_status_code}}}} err={{{{.attributes_error}}}} session={{{{.attributes_session_id}}}}"')
panels += [{
    "id": nid(), "type": "logs", "title": "API errors & failed tool calls", "datasource": LOKI,
    "gridPos": {"h": 8, "w": 24, "x": 0, "y": y},
    "targets": [{"refId": "A", "expr": err_q, "queryType": "range"}],
    "options": {"showTime": True, "wrapLogMessage": True, "sortOrder": "Descending", "enableLogDetails": True,
                "dedupStrategy": "none", "prettifyLogMessage": False, "showLabels": False, "showCommonLabels": False},
    "description": "Raw events: claude_code.api_error plus tool_result with success=false. Expand a line for every attribute.",
}]

dashboard = {
    "uid": "claude-code-usage",
    "title": "Claude Code Usage",
    "description": "Claude Code OpenTelemetry usage: cost, tokens, sessions, latency, tools. Source: OTLP → Alloy → Prometheus (metrics) + Loki (events).",
    "tags": ["claude-code", "telemetry"],
    "timezone": "browser",
    "editable": True,
    "graphTooltip": 1,
    "schemaVersion": 39,
    "refresh": "1m",
    "time": {"from": "now-7d", "to": "now"},
    "templating": {"list": [{
        "name": "host", "label": "Host", "type": "query", "datasource": PROM,
        "query": {"query": 'label_values(claude_code_session_count, host_name)', "refId": "hosts"},
        "definition": 'label_values(claude_code_session_count, host_name)',
        "refresh": 2, "includeAll": True, "allValue": ".*", "multi": True, "sort": 1,
        "current": {"text": ["All"], "value": ["$__all"]},
    }]},
    "links": [{"title": "Claude Code monitoring docs", "type": "link", "targetBlank": True,
               "url": "https://docs.anthropic.com/en/docs/claude-code/monitoring-usage"}],
    "panels": panels,
}

HEADER = '''# Grafana dashboard for Claude Code usage telemetry.
#
# GENERATED — do not hand-edit the JSON. Edit claude-code-dashboard.gen.py
# (same directory) and run:
#   python3 claude-code-dashboard.gen.py claude-code-dashboard.yaml
# Background + client setup: docs/claude-code-telemetry.md
#
# Discovery: the kube-prometheus-stack grafana-sc-dashboard sidecar watches
# ConfigMaps labeled grafana_dashboard=1 in ALL namespaces and hot-loads the
# JSON — no Grafana restart, no sqlite state. Provisioned dashboards survive
# PV wipes (the 2026-07-10 cutover destroyed the previous UI-created one).
#
# Data path: Claude Code (OTLP http/protobuf, see ~/.claude/settings.json on
# each box) → Alloy otelcol.receiver.otlp :4318 → prometheus.remote_write
# (metrics) + Loki (events).
#
# Gotchas baked into the queries:
#   * Alloy's prometheus exporter adds NO _total/unit suffixes, so metric
#     names are the bare converted OTLP names (claude_code_cost_usage, ...).
#   * Claude Code >= 2.x exports DELTA temporality by default and Alloy's
#     prometheus exporter silently drops delta sums (only target_info gets
#     through). Clients MUST set
#     OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE=cumulative.
#   * Counters are cumulative PER SESSION (label session_id), so increase()
#     and rate() under-count badly (first sample already carries value, short
#     sessions have one sample). Every window query uses the exact form
#       (last_in_window - (last_before_window and last_in_window)) or last_in_window
#     which attributes each session's growth to the right bucket.
#   * Loki events are JSON lines {"body": "claude_code.<event>", "attributes":
#     {...}}; `| json` flattens them to attributes_<name> (dots → underscores).
'''


def main(out):
    js = json.dumps(dashboard, indent=2, ensure_ascii=False)
    body = "\n".join("    " + line if line else "" for line in js.splitlines())
    yaml = (HEADER +
            "apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: claude-code-dashboard\n  namespace: observability\n"
            "  labels:\n    grafana_dashboard: \"1\"\ndata:\n  claude-code-usage.json: |\n" + body + "\n")
    with open(out, "w") as f:
        f.write(yaml)
    # also dump queries for the tester
    qs = []
    for p in panels:
        for t in p.get("targets", []):
            qs.append({"panel": p["title"], "ds": p["datasource"]["type"], "expr": t["expr"],
                       "instant": bool(t.get("instant") or t.get("queryType") == "instant")})
    with open(out + ".queries.json", "w") as f:
        json.dump(qs, f, indent=1)
    print(f"wrote {out}: {len(panels)} panels, {len(qs)} queries")


if __name__ == "__main__":
    main(sys.argv[1])
