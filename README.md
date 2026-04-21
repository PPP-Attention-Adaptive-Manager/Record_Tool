# Experiment Data Collection System

This repo is split into two parts:

- `system_agent/`: the local Python core
- `browser_agent_v2/`: the Chrome extension

The Python core is the authority. It creates sessions, accepts browser events, writes CSV files into `data/`, and can optionally mirror those events to InfluxDB.

During an active session, the Python core now captures three local system streams under the same `session_id`:

- app/window focus events
- keyboard events
- mouse events

## What The Normal Flow Looks Like

1. Start the Python core in a terminal
2. Load or reload the Chrome extension
3. Start a session from the extension popup
4. Browse normally
5. Stop the session from the extension popup
6. Fill out the questionnaire tab that opens
7. Stop the Python core with `Ctrl+C` or the popup `Quit Core` button

`Stop Session` ends recording.

`Quit Core` shuts down the local Python server.

## Prerequisites

- Windows with PowerShell
- Python 3.10+
- Google Chrome
- Optional: InfluxDB 2.x

## Project Paths

From your machine, the repo root is:

```powershell
c:\GL3\semester 2\PPP\tp\Recording_tool_V2\Record_Tool
```

All commands below assume you are inside that folder.

## First-Time Setup

### 1. Open a terminal in the repo

```powershell
cd "c:\GL3\semester 2\PPP\tp\Recording_tool_V2\Record_Tool"
```

### 2. Create the virtual environment

```powershell
py -3.13 -m venv .venv
```

If that hangs or was interrupted before, remove the broken env and recreate it:

```powershell
Remove-Item -Recurse -Force .venv -ErrorAction SilentlyContinue
py -3.13 -m venv .venv
```

If `venv` still fails during `ensurepip`, use:

```powershell
py -3.13 -m venv .venv --without-pip
.\.venv\Scripts\Activate.ps1
python -m ensurepip --upgrade
python -m pip install --upgrade pip
```

### 3. Activate the virtual environment

```powershell
.\.venv\Scripts\Activate.ps1
```

You should now see `(.venv)` in the terminal prompt.

### 4. Install dependencies

```powershell
python -m pip install --upgrade pip
pip install -r system_agent\requirements.txt
```

## Optional InfluxDB Setup

You only need this if you want the Python core to mirror CSV events into InfluxDB.

Create `Record_Tool/.env` with:

```env
INFLUX_URL=http://localhost:8086
INFLUX_TOKEN=your-token
INFLUX_ORG=research
INFLUX_BUCKET=behavior
```

If any of those are missing, the app still works and writes CSV only.

## Start The Python Core

With the virtualenv activated:

```powershell
python system_agent\main.py --host 127.0.0.1 --port 8765
```

If it starts correctly, the core listens on:

```text
http://127.0.0.1:8765
```

The main API endpoints are:

- `GET /health`
- `GET /session/status`
- `POST /session/start`
- `POST /events`
- `POST /session/stop`
- `POST /shutdown`

CSV files are written to:

```text
Record_Tool/data/
```

## Load The Chrome Extension

### 1. Open Chrome extensions

Go to:

```text
chrome://extensions
```

### 2. Enable Developer mode

Turn on the toggle in the top-right.

### 3. Remove or disable any older copy first

This matters if you previously loaded:

- `Record_Tool/extension`
- an older copy of `browser_agent_v2`

### 4. Load the correct extension folder

Click `Load unpacked` and choose:

```text
c:\GL3\semester 2\PPP\tp\Recording_tool_V2\Record_Tool\browser_agent_v2
```

### 5. Reload it after code changes

If you already loaded it once and changed the code, press `Reload` on the extension card.

## Use The Extension Popup

When you open the popup, you should see:

- a participant ID field
- a session duration field
- an optional Influx checkbox
- a `Core settings` section

You should not need to fill InfluxDB connection fields in the extension.

### Participant ID format

Use this format:

```text
P001
P002
P123
```

## Run A Session

### 1. Make sure the Python core is already running

If the core is not running, the popup will show offline/buffering behavior and queued events.

### 2. Open the popup

Click the extension icon in Chrome.

### 3. Enter session data

- Participant ID: example `P001`
- Duration: example `30`
- Optional: enable `InfluxDB mirror on the core if configured`

### 4. Start the session

Click `Start Session`.

### 5. Browse normally

The extension will send events to the Python core.

### 6. Stop the session

Click `Stop Session`.

### 7. Fill the questionnaire

After stopping, a questionnaire tab opens. Submit it to complete the session data.

## Stop The Core

You now have two supported ways to stop the Python core.

### Option 1. From the terminal

Press:

```text
Ctrl+C
```

This should now cleanly shut down the core and return the shell prompt.

### Option 2. From the popup

Click:

```text
Quit Core
```

Notes:

- `Quit Core` is hidden while a session is active
- stop the session first, then quit the core
- if the core is already offline, the button is disabled

## Quick Health Check

With the core running:

```powershell
curl http://127.0.0.1:8765/health
```

Expected shape:

```json
{"status":"healthy","version":"1.0.0","uptime_seconds":12.345}
```

## Manual Session API Check

Start a session manually:

```powershell
curl -Method POST http://127.0.0.1:8765/session/start `
  -ContentType 'application/json' `
  -Body '{"user_id":"P001","duration_minutes":30,"enable_influx":false}'
```

Check active status:

```powershell
curl http://127.0.0.1:8765/session/status
```

Stop the session manually:

```powershell
curl -Method POST http://127.0.0.1:8765/session/stop `
  -ContentType 'application/json' `
  -Body '{"session_id":"YOUR_SESSION_ID"}'
```

## Where To Look For Output

### CSV output

Files are created in:

```text
Record_Tool/data/
```

Example:

```text
data/sess_20260421_133223_616fad.csv
```

The same session CSV now includes:

- browser events from the extension
- `app_focus` events from the desktop collector
- `keyboard_input` events
- `mouse_input` events

Keyboard and mouse rows share the same `session_id` as the browser and app-focus rows for that session.

### Event schema

The canonical schema is:

```text
system_agent/schemas/event_schema.json
```

Some useful keyboard/mouse columns you will now see in CSV:

- `input_device`
- `input_action`
- `key_value`
- `button`
- `pressed`
- `pointer_x`
- `pointer_y`
- `wheel_delta_x`
- `wheel_delta_y`

## Troubleshooting

### The popup still asks for old InfluxDB settings

Chrome is almost certainly loading the wrong extension build.

Fix:

1. Open `chrome://extensions`
2. Remove or disable the old extension
3. Load `Record_Tool/browser_agent_v2`
4. Click `Reload`

You should see `Core settings`, not old Influx-only settings.

### The popup shows `offline`

Check these in order:

1. Is the Python core running?
2. Is the popup `Core URL` set to `http://localhost:8765` or `http://127.0.0.1:8765` consistently with how you started it?
3. Did you reload the extension after code changes?

### Events are queued but not flushed

That means the extension is keeping data locally and retrying.

- queued events are stored in IndexedDB
- once the core comes back, they retry automatically

### `Ctrl+C` does not seem to work

It should now stop the controllable server cleanly. If the terminal still does not return:

1. wait 1-2 seconds
2. press `Ctrl+C` again
3. if needed, close the terminal or kill Python from another shell:

```powershell
Get-Process python | Stop-Process -Force
```

### CSV is not updating

Check:

- the core terminal logs
- `GET /session/status`
- that the session actually started
- that the participant ID matches `P001` format

### The extension changed but Chrome still behaves the old way

Reload the unpacked extension from `chrome://extensions`.

## Tests

If your environment has pytest installed:

```powershell
pytest system_agent\tests
```

## Summary

If you just want the shortest happy path:

```powershell
cd "c:\GL3\semester 2\PPP\tp\Recording_tool_V2\Record_Tool"
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r system_agent\requirements.txt
python system_agent\main.py --host 127.0.0.1 --port 8765
```

Then in Chrome:

1. Load `browser_agent_v2`
2. Enter `P001`
3. Start session
4. Stop session
5. Submit questionnaire
6. Quit core or press `Ctrl+C`
