# Cognitive Behavior Collection System

This project is split into two components:

- `system_agent/`: master orchestrator and single source of truth for session timing/state
- `browser_agent_v2/`: passive browser collector + UI display, driven by system-agent commands

## Corrected Architecture

System agent responsibilities:

- Startup configuration flow:
  - mode (`experimental` or `production`)
  - session duration
  - CSV export enabled
  - Influx export enabled
  - dual-task enabled
  - questionnaire enabled
- Global session timer ownership (elapsed/remaining)
- Browser foreground detection (OS active-window tracking)
- Recording orchestration commands:
  - `start_recording` / `resume_recording` when browser is foreground
  - `pause_recording` when browser leaves foreground
  - `stop_recording` on session end
- Periodic dual-task probe triggering (experimental mode only)
- Session-end questionnaire trigger in browser

Extension responsibilities:

- Record browser events only when instructed
- Display agent-provided session status:
  - `inactive` / `running` / `paused`
  - session id
  - mode
  - elapsed time
  - remaining time
  - browser recording active
- Show reaction-time probe overlay when requested by agent
- Open browser questionnaire page on `open_questionnaire`

## Session Output

Per session:

- `data/<session_id>/behavior.csv`
- `data/<session_id>/keyboard.csv`
- `data/<session_id>/mouse.csv`
- `data/<session_id>/labels.csv`

## Dependency Policy

No silent degraded mode for critical runtime dependencies:

- `websockets` and `aiohttp` are required
- if Influx is enabled, `influxdb-client` is required
- if keyboard/mouse tracking is enabled, `pynput` is required
- `psutil` is required for active-app browser foreground detection

## Run

From `cognitive_system/`:

```bash
python setup.py
.venv/Scripts/python system_agent/main.py
```

Then load unpacked extension:

- Chrome -> `chrome://extensions`
- Enable Developer Mode
- Load unpacked -> `cognitive_system/browser_agent_v2`

