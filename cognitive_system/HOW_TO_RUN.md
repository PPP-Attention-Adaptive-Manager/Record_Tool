1. Edit the shared config if needed

```bash
browser_agent_v2/config/runtime_config.json
```

2. Install dependencies

```bash
cd cognitive_system
python setup.py
```

3. Start the system agent

```bash
.venv/Scripts/python system_agent/main.py
```

4. Complete startup configuration in terminal

- mode
- duration
- csv enabled
- influx enabled
- dual-task enabled
- questionnaire enabled

5. Press Enter to start the session

6. Load the extension

- Open `chrome://extensions`
- Enable Developer mode
- Load unpacked: `cognitive_system/browser_agent_v2`

Behavior summary:

- System agent owns session timing
- Extension only displays elapsed/remaining from agent updates
- Recording starts/resumes/pauses automatically based on browser foreground
- In experimental mode, reaction probes appear periodically as clickable squares
- At session end, questionnaire opens in browser tab and submits to system agent
