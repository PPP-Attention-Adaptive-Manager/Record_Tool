# Cognitive System

`cognitive_system/` contains the full collection, storage, analysis, and visualization stack for behavioral session modeling.

## Main Components

- `system_agent/`
  - Python orchestrator running on the desktop
  - Owns session timing, active-app tracking, CSV writing, and runtime coordination
  - Collects app/context events, keyboard, mouse, notification, system metrics, and dual-task results
  - Supports regular or random dual-task probe timing, with randomized screen position by default

- `browser_agent_v2/`
  - Chrome extension
  - Sends browser navigation, tab, active/idle, and scroll events to the system agent
  - Displays runtime status from the agent

- `feature_engineering/`
  - Builds session windows
  - Extracts per-window features
  - Builds corrected temporal graphs from sequential events
  - Exports per-window graph slices
  - Clusters windows into cognitive-state labels when clustering dependencies are installed
  - Provides a Tkinter viewer for one-session window-graph inspection

- `data/`
  - Stores per-session runtime outputs and derived analysis artifacts

## Runtime Data Flow

1. `system_agent/` starts a session and opens `data/<session_id>/raw/`
2. `browser_agent_v2/` streams browser events to the agent
3. `system_agent/` merges context state and writes raw CSV streams
4. `feature_engineering.pipeline` loads the raw session streams
5. Window features are generated for `5s`, `30s`, and `120s` windows
6. The graph builder creates event-to-event temporal transitions from `behavior.csv`
7. Secondary window-level graph slices are exported under `graph/windows/<label>/`
8. The graph viewer renders one session as a table of window graphs

## Session Directory Layout

Typical output for one session:

```text
data/<session_id>/
|- raw/
|  |- behavior.csv
|  |- keyboard.csv
|  |- mouse.csv
|  |- dual_task.csv
|  |- notification.csv
|  |- system_metrics.csv
|  `- labels.csv
|- features/
|  |- features_5s.csv
|  |- features_30s.csv
|  `- features_120s.csv
`- graph/
   |- nodes.csv
   |- edges.csv
   |- temporal_edges.csv
   |- communities.csv
   `- windows/
      |- 5s/
      |- 30s/
      `- 120s/
```

## Temporal Graph Semantics

The graph builder does not use `window_id` as a graph node.

Instead:

- source node = state resolved from `event[i]`
- target node = state resolved from `event[i+1]`
- temporal edge timestamp = timestamp of the transition event

Supported node granularities:

- `app`
- `domain`
- `url`

Main exports:

- `graph/nodes.csv`
  - `node_id,node_type`
- `graph/edges.csv`
  - `source,target,transition_count,total_duration,avg_duration`
- `graph/temporal_edges.csv`
  - `source,target,timestamp`

## Viewer

The viewer is intended for session-level exploration of window graphs.

Run:

```powershell
.\.venv\Scripts\python -m feature_engineering.graph_viewer --session-id <session_id> --window-label 30s
```

Current viewer behavior:

- shows one session at a time
- displays windows in a table/grid
- renders one graph per window
- lets you click nodes and edges to inspect features
- shows `N/A` for node or edge features that the pipeline does not compute yet

## Dependencies

Recommended install path:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

`requirements.txt` includes both runtime and analysis dependencies. `setup.py` is still available as a quick helper, but it installs only the `system_agent` requirements.

After dependencies are installed on Windows, users can also start the desktop launcher by double-clicking `run_collector.pyw`.

## See Also

- Run instructions: [HOW_TO_RUN.md](HOW_TO_RUN.md)
- Full architecture: [../Architecture.md](../Architecture.md)
