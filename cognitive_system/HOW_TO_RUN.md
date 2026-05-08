# How To Run

This guide covers three workflows:

1. collecting a session
2. running the analysis pipeline
3. opening the window graph viewer

## 1. Setup

From `cognitive_system/`:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install --upgrade pip
pip install -r requirements.txt
```

Notes:

- `requirements.txt` is the recommended install target because it includes both runtime and feature-engineering dependencies
- `python setup.py` is still usable, but it installs only the `system_agent` dependency file

## 2. Configure Shared Runtime Settings

Edit:

```text
browser_agent_v2/config/runtime_config.json
```

This file controls shared runtime values such as:

- websocket host and port
- HTTP host and port
- default agent mode
- default duration
- default Influx settings
- dual-task timing defaults and randomization behavior

Dual-task note:

- `dual_task_interval_mode` chooses `regular` or `random` timing
- `dual_task_interval_seconds` is used when timing is regular
- `dual_task_random_min_seconds` and `dual_task_random_max_seconds` define the random interval range
- `dual_task_randomize_position` randomizes where the probe window appears on screen

Default dual-task timing is random between 180 and 360 seconds.

If you enable Influx export, keep `INFLUXDB_TOKEN` in your environment.

## 3. Start The Desktop Application

From `cognitive_system/`:

```powershell
.\.venv\Scripts\python -m system_agent
```

This is the recommended entry point for other users because it opens the desktop launcher.

Windows Explorer option:

- after dependencies are installed, you can also launch the same desktop app by double-clicking `run_collector.pyw`

The launcher lets the user choose:

- mode
- session duration
- CSV export enabled or disabled
- Influx export enabled or disabled
- dual-task enabled or disabled
- dual-task interval timing
- questionnaire enabled or disabled

During an experimental session, `Ctrl+C` opens the questionnaire immediately even if the planned duration is not finished. `Ctrl+Shift+Q` remains available as a fallback shortcut.

CLI fallback:

```powershell
.\.venv\Scripts\python system_agent\main.py
```

You can also run it non-interactively with CLI flags defined in `system_agent/config.py`.

## 4. Load The Chrome Extension

In Chrome:

1. Open `chrome://extensions`
2. Enable Developer mode
3. Click Load unpacked
4. Select `cognitive_system/browser_agent_v2`

In Edge, use the same steps from `edge://extensions`.

What happens at runtime:

- the system agent owns session timing
- the extension only records when the agent instructs it to
- browser recording pauses and resumes based on browser foreground status
- dual-task probes may appear depending on the selected mode, with randomized timing and position
- the questionnaire is opened by the agent at session end when enabled
- the popup should show `online` once the extension connects to the running desktop app

If you change `browser_agent_v2/config/runtime_config.json`, restart the desktop app and reload the extension.

## 5. Check Raw Session Output

After or during a run, inspect:

```text
data/<session_id>/raw/
```

Typical files:

- `behavior.csv`
- `keyboard.csv`
- `mouse.csv`
- `dual_task.csv`
- `notification.csv`
- `system_metrics.csv`
- `labels.csv`

## 6. Run The Feature Engineering Pipeline

After a session finishes:

```powershell
.\.venv\Scripts\python -m feature_engineering.pipeline <session_id> --graph-node-level app
```

Useful options:

```powershell
.\.venv\Scripts\python -m feature_engineering.pipeline <session_id> --graph-node-level domain
.\.venv\Scripts\python -m feature_engineering.pipeline <session_id> --primary-window 30s
.\.venv\Scripts\python -m feature_engineering.pipeline <session_id> --skip-graph
```

What the pipeline does:

1. loads all raw session CSVs
2. creates windows for `5s`, `30s`, and `120s`
3. writes `features/features_<label>.csv`
4. builds the main temporal graph from sequential behavior events
5. exports per-window graph slices under `graph/windows/<label>/`
6. attempts clustering for the primary window set

## 7. Validate The Graph Output

Main graph files:

```text
data/<session_id>/graph/nodes.csv
data/<session_id>/graph/edges.csv
data/<session_id>/graph/temporal_edges.csv
```

Window graph files:

```text
data/<session_id>/graph/windows/5s/
data/<session_id>/graph/windows/30s/
data/<session_id>/graph/windows/120s/
```

Important validation rule:

- graph edges must be state-to-state transitions such as `code.exe -> chrome.exe`
- graph edges must not be `w000001 -> w000002`

## 8. Open The Window Graph Viewer

To inspect one session as a table of windows:

```powershell
.\.venv\Scripts\python -m feature_engineering.graph_viewer --session-id <session_id> --window-label 30s
```

Useful options:

```powershell
.\.venv\Scripts\python -m feature_engineering.graph_viewer --window-label 5s
.\.venv\Scripts\python -m feature_engineering.graph_viewer --tab-level url
.\.venv\Scripts\python -m feature_engineering.graph_viewer --columns 4
```

Viewer behavior:

- shows one session at a time
- lays out windows in a table
- draws one graph per window
- clicking a node opens node features in the inspector
- clicking an edge opens edge features in the inspector

## 9. Troubleshooting

If the viewer starts but a window has no graph:

- check whether `behavior.csv` contains usable context events
- check whether the selected window actually contains any events

If clustering is skipped:

- ensure `scikit-learn` is installed
- confirm you installed from `requirements.txt`, not only from `system_agent/requirements.txt`

If the system agent starts but the extension appears inactive:

- confirm the extension is loaded from `browser_agent_v2/`
- confirm the browser is one of the tracked foreground processes
- confirm the agent is still running and the selected ports match `runtime_config.json`
