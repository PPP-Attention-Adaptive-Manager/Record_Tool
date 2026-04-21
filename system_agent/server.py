import logging
import time
import uuid
from datetime import datetime, timedelta
from flask import Flask, request, jsonify
from flask_cors import CORS
from csv_writer import CSVWriter

app = Flask(__name__)
CORS(app)

# Configuration
VERSION = "1.0.0"
PORT = 8765

# State
active_session = {
    "active": False,
    "session_id": None,
    "user_id": None,
    "started_at": None,
    "expires_at": None,
    "events_received": 0,
    "csv_path": None
}

csv_writer = CSVWriter()

def generate_session_id():
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    short_uuid = uuid.uuid4().hex[:6]
    return f"sess_{ts}_{short_uuid}"

@app.route('/health', methods=['GET'])
def health():
    return jsonify({
        "status": "healthy",
        "version": VERSION,
        "uptime_seconds": int(time.time() - app.start_time)
    })

@app.route('/session/start', methods=['POST'])
def start_session():
    global active_session
    data = request.json
    
    if active_session["active"]:
        return jsonify({
            "error": "session_active",
            "current_session_id": active_session["session_id"],
            "message": "Stop current session before starting new one"
        }), 409
    
    user_id = data.get("user_id", "P001")
    duration_minutes = data.get("duration_minutes", 60)
    
    session_id = generate_session_id()
    started_at = datetime.now().isoformat() + "Z"
    expires_at = (datetime.now() + timedelta(minutes=duration_minutes)).isoformat() + "Z"
    
    csv_path = csv_writer.start_session(session_id)
    
    active_session = {
        "active": True,
        "session_id": session_id,
        "user_id": user_id,
        "started_at": started_at,
        "expires_at": expires_at,
        "events_received": 0,
        "csv_path": csv_path
    }
    
    # Write start_session event
    start_event = {
        "session_id": session_id,
        "user_id": user_id,
        "event_id": f"evt_{uuid.uuid4().hex[:8]}",
        "event_type": "start_session",
        "timestamp": int(time.time() * 1000),
        "source": "system"
    }
    csv_writer.write_event(start_event)
    active_session["events_received"] += 1
    
    logging.info(f"Session started: {session_id} for user {user_id}")
    
    return jsonify({
        "session_id": session_id,
        "started_at": started_at,
        "expires_at": expires_at,
        "csv_path": csv_path
    })

@app.route('/session/stop', methods=['POST'])
def stop_session():
    global active_session
    data = request.json
    
    if not active_session["active"]:
        return jsonify({
            "error": "no_active_session",
            "message": "No session to stop"
        }), 404
    
    session_id = data.get("session_id")
    if session_id and session_id != active_session["session_id"]:
        return jsonify({
            "error": "session_mismatch",
            "message": "Provided session_id does not match active session"
        }), 409
    
    # Write end_session event
    end_event = {
        "session_id": active_session["session_id"],
        "user_id": active_session["user_id"],
        "event_id": f"evt_{uuid.uuid4().hex[:8]}",
        "event_type": "end_session",
        "timestamp": int(time.time() * 1000),
        "source": "system"
    }
    csv_writer.write_event(end_event)
    active_session["events_received"] += 1
    
    stopped_at = datetime.now().isoformat() + "Z"
    duration_seconds = (datetime.now() - datetime.fromisoformat(active_session["started_at"].replace("Z", ""))).total_seconds()
    
    events_written = active_session["events_received"]
    old_session_id = active_session["session_id"]
    
    csv_writer.stop_session()
    
    active_session = {
        "active": False,
        "session_id": None,
        "user_id": None,
        "started_at": None,
        "expires_at": None,
        "events_received": 0,
        "csv_path": None
    }
    
    logging.info(f"Session stopped: {old_session_id}")
    
    return jsonify({
        "session_id": old_session_id,
        "stopped_at": stopped_at,
        "duration_seconds": duration_seconds,
        "events_written": events_written
    })

@app.route('/events', methods=['POST'])
def ingest_events():
    global active_session
    data = request.json
    
    if not active_session["active"]:
        return jsonify({
            "error": "no_active_session",
            "message": "No active session to ingest events"
        }), 404
    
    session_id = data.get("session_id")
    if session_id != active_session["session_id"]:
        return jsonify({
            "error": "session_mismatch",
            "message": "Event session_id does not match active session"
        }), 409
    
    events = data.get("events", [])
    if not isinstance(events, list):
        events = [events]
    
    # Basic validation and enrichment
    for event in events:
        if "event_type" not in event:
            return jsonify({
                "error": "invalid_payload",
                "details": "Missing required field: event_type"
            }), 400
        
        # Ensure session_id and user_id are present
        event["session_id"] = active_session["session_id"]
        event["user_id"] = active_session["user_id"]
        
        if "event_id" not in event:
            event["event_id"] = f"evt_{uuid.uuid4().hex[:8]}"
        
        if "timestamp" not in event:
            event["timestamp"] = int(time.time() * 1000)

    accepted_count = csv_writer.write_events(events)
    active_session["events_received"] += accepted_count
    
    return jsonify({
        "accepted": accepted_count,
        "session_id": active_session["session_id"]
    })

@app.route('/session/status', methods=['GET'])
def get_status():
    return jsonify(active_session)

if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    app.start_time = time.time()
    app.run(host='0.0.0.0', port=PORT)
