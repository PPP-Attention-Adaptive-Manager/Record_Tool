# Behavior User Collecting

Behavior User Collecting is a desktop-plus-browser data collection project.

It includes:

- a Windows desktop application that controls the collection session
- a Chromium browser extension that sends browser activity to the desktop application
- a Python analysis pipeline that turns sessions into features and temporal graphs

The main project code lives in [cognitive_system/](cognitive_system/README.md).

## Intended Platform

This repository is designed for local use on a Windows machine.

Recommended prerequisites:

- Windows 10 or 11
- Python 3.10+
- Google Chrome or Microsoft Edge

## Installation

From the repository root:

```powershell
cd .\cognitive_system
python -m venv .venv
.\.venv\Scripts\python -m pip install --upgrade pip
.\.venv\Scripts\python -m pip install -r requirements.txt
```

## Configuration

The desktop application and the browser extension share the same runtime configuration file:

```text
cognitive_system/browser_agent_v2/config/runtime_config.json
```

This file contains shared settings such as:

- WebSocket host and port
- HTTP host and port
- default mode
- default session duration
- dual-task timing and position behavior

Important:

- if you change the ports, restart the desktop application
- after changing this file, reload the browser extension as well

InfluxDB is optional. CSV export is enough for normal local use.

## Launch The Application

From `cognitive_system/`, start the desktop launcher with:

```powershell
.\.venv\Scripts\python -m system_agent
```

This opens the desktop launcher, where the user can choose:

- mode (`experimental` or `production`)
- session duration
- collection options

Windows users can also launch the same desktop application by double-clicking:

```text
cognitive_system/run_collector.pyw
```

Use that option only after the Python dependencies have already been installed.

Terminal fallback:

```powershell
.\.venv\Scripts\python system_agent\main.py
```

Useful notes:

- the desktop application starts the local servers used by the extension
- browser recording is controlled automatically by the desktop application
- when the browser is no longer the foreground app, browser recording pauses
- dual-task probes are randomized in timing and screen position by default

## Load The Extension

In Chrome:

1. Open `chrome://extensions`
2. Enable `Developer mode`
3. Click `Load unpacked`
4. Select the `cognitive_system/browser_agent_v2` folder

In Edge:

1. Open `edge://extensions`
2. Enable `Developer mode`
3. Click `Load unpacked`
4. Select the `cognitive_system/browser_agent_v2` folder

Tips:

- pin the extension so the popup is easy to access
- the popup should show `online` when the desktop application is running
- if the extension stays `offline`, check that the desktop application is running and that the ports match `runtime_config.json`

## First Run

Recommended order for a new user:

1. Install the Python dependencies.
2. Launch the desktop application.
3. Load the extension in Chrome or Edge.
4. Return to the launcher window and start the session.

During the session:

- the timer is controlled by the desktop application
- the extension sends browser events to the application
- a questionnaire may open at the end of the session if that option is enabled

## Data Output

Raw files are written to:

```text
cognitive_system/data/<session_id>/raw/
```

Typical outputs:

- `behavior.csv`
- `keyboard.csv`
- `mouse.csv`
- `dual_task.csv`
- `notification.csv`
- `system_metrics.csv`
- `labels.csv`

## Run The Analysis Pipeline

After a session is finished:

```powershell
cd .\cognitive_system
.\.venv\Scripts\python -m feature_engineering.pipeline <session_id> --graph-node-level app
```

To open the graph viewer:

```powershell
.\.venv\Scripts\python -m feature_engineering.graph_viewer --session-id <session_id> --window-label 30s
```

## Repository Layout

```text
Behavior User Collecting/
|- README.md
|- Architecture.md
|- cognitive_system/
|  |- README.md
|  |- HOW_TO_RUN.md
|  |- requirements.txt
|  |- run_collector.pyw
|  |- data/
|  |- browser_agent_v2/
|  |- system_agent/
|  `- feature_engineering/
`- .env.example
```

## Additional Documentation

- Technical overview: [cognitive_system/README.md](cognitive_system/README.md)
- Step-by-step run guide: [cognitive_system/HOW_TO_RUN.md](cognitive_system/HOW_TO_RUN.md)
- Full architecture: [Architecture.md](Architecture.md)
