1. Install dependencies

```bash
cd cognitive_system
python setup.py
```

2. Start the system agent

```bash
.venv/Scripts/python system_agent/main.py
```

3. Complete startup configuration in terminal

- mode
- duration
- csv enabled
- influx enabled
- dual-task enabled
- questionnaire enabled

4. Press Enter to start the session

5. Load the extension

- Open `chrome://extensions`
- Enable Developer mode
- Load unpacked: `cognitive_system/browser_agent_v2`

Behavior summary:

- System agent owns session timing
- Extension only displays elapsed/remaining from agent updates
- Recording starts/resumes/pauses automatically based on browser foreground
- In experimental mode, reaction probes appear periodically as clickable squares
- At session end, questionnaire opens in browser tab and submits to system agent

