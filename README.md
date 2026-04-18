# Local Behavior Collection to InfluxDB v2

This project contains two collectors that write raw behavioral events directly to InfluxDB v2 using line protocol (no JSON storage layer):

- `system_agent/`: desktop app/input collector in Python
- `extension/`: Chrome extension for browser tab/scroll/activity tracking

## 1) Configure Environment

1. Copy `.env.example` to `.env`.
2. Fill in:
   - `INFLUX_URL`
   - `INFLUX_TOKEN`
   - `INFLUX_ORG`
   - `INFLUX_BUCKET`

For the extension, copy the same env file into `extension/.env`:

```powershell
Copy-Item .env extension/.env
```

## 2) Run System Agent

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r system_agent/requirements.txt
python system_agent/main.py
```

## 3) Load Chrome Extension

1. Open `chrome://extensions`.
2. Enable **Developer mode**.
3. Click **Load unpacked**.
4. Select the `extension/` folder.

## 4) Verify in InfluxDB

You can verify writes in Influx Data Explorer or with Flux:

```flux
from(bucket: "YOUR_BUCKET")
  |> range(start: -15m)
  |> filter(fn: (r) => r._measurement == "behavior_events")
  |> sort(columns: ["_time"], desc: true)
```

## Event Tags and Fields

Common tags:
- `user_id` (`u1`)
- `source_type` (`app` or `tab`)
- `event_type` (`focus`, `switch`, `input`, `scroll`)

Additional tags:
- System agent: `app_name`
- Browser agent: `domain`

Common fields include:
- `duration`
- `keystrokes`
- `mouse_speed`
- `scroll_delta`
- `scroll_depth`
- `clicks`
- `window_title` (system agent)

