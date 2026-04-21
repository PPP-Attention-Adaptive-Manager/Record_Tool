# Cognitive Behavior Data Collection System

Production-oriented, synchronized multi-component collection stack with:

- System-level behavior tracking (application switches, browser focus)
- Browser behavior extension stream (tabs, navigation, scroll aggregation, focus/idle/background)
- Independent keyboard stream
- Independent mouse stream
- Experimental signals (dual-task reaction probes)
- Subjective labels (NASA-TLX + stress + emotion)

No machine learning is implemented.

## Architecture

System Agent (`system_agent/`) is the single source of truth:

1. Controls session lifecycle and timing
2. Commands passive browser extensions (`start/pause/resume/stop_recording`)
3. Synchronizes extension timestamps to system time
4. Merges streams and writes to:
   - InfluxDB buckets: `behavior_bucket`, `keyboard_bucket`, `mouse_bucket`
   - Session CSV folder: `data/<session_id>/`

Browser extension (`browser_agent_v2/`) is passive:

- Polls command channel through heartbeat
- Collects browser events
- Aggregates scroll metrics and flushes only on checkpoints
- Sends event batches with `device_id` and `session_id`

## Mode Toggle

Set `MODE` in environment:

- `experimental`: dual-task + questionnaire enabled
- `production`: dual-task + questionnaire disabled

## Run

1. Install Python dependencies:

```bash
pip install -r requirements.txt
```

2. Start system agent from `cognitive_system/`:

```bash
python -m system_agent.main --duration-minutes 30
```

3. Load browser extension:

- Open browser extension page (developer mode)
- Load unpacked folder: `cognitive_system/browser_agent_v2`

## Session Output

For each session:

- `data/<session_id>/behavior.csv`
- `data/<session_id>/keyboard.csv`
- `data/<session_id>/mouse.csv`
- `data/<session_id>/labels.csv`

## Communication API (localhost:5000)

- `POST /v1/extensions/heartbeat`
- `POST /v1/extensions/events`
- `GET /v1/health`

