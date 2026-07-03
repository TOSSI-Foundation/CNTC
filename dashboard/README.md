# upfbench dashboard

A live, **view-only** Plotly Dash app over `campaigns/` — showcases the framework and
browses every result. Reads `campaigns/` fresh on each page load, so new runs appear
without a restart.

## Run

No package install needed — run it straight from the repo root (dash + plotly are the
only deps):

```bash
# one-time: just the two libraries
pip install --user dash plotly

# start it (from the repo root)
python3 -m dashboard.app                 # http://<host>:8050
UPFBENCH_DASH_PORT=9000 python3 -m dashboard.app
UPFBENCH_DASH_DEBUG=1   python3 -m dashboard.app   # hot-reload while editing
```

Then open `http://<server-ip>:8050`.

> The `[dashboard]` extra exists (`pip install -e '.[dashboard]'`), but editable installs
> need `setuptools >= 64` (PEP 660). If you see a `build_editable` error, either run it
> the no-install way above, or upgrade first: `pip install --user -U setuptools`.

## Pages

| Route | What |
|---|---|
| `/` Overview | What upfbench is, headline KPIs, the N3 robustness finding |
| `/campaigns` | Every campaign on disk (newest first) → click to drill in |
| `/campaign/<key>` | One campaign: SUT card + every suite with charts/tables/notes |
| `/compare` | Overlay throughput across modes (DPDK vs AF_XDP vs CNDP …) |
| `/catalog` | The full test catalog (16 cases) + standard each maps to |
| `/methodology` | Injection topology, egress short-circuit, plugin architecture |

## Layout

- `data.py` — scans/normalizes `campaigns/*/results.json` into a typed model. **Single
  source of truth**; every page reads from here.
- `theme.py` — colors + the Plotly template (one visual language).
- `charts.py` — Plotly figure builders keyed by test id (`TC-01`, `LT-02`, `NT-02`, …).
- `components.py` — reusable Dash html (cards, KPI tiles, pills, tables, test panels).
- `pages/` — one module per route (`dash.register_page`).
- `assets/style.css` — auto-served dark theme.

View-only by design (v1): it never triggers runs. To add a result, run a suite with the
CLI; it shows up on next page load.
