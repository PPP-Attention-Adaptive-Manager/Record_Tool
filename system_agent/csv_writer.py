import csv
import os
import logging
from pathlib import Path
from typing import Dict, Any, List

class CSVWriter:
    def __init__(self, data_dir: str = "data"):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.current_file = None
        self.writer = None
        self.file_handle = None
        self.fieldnames = [
            "session_id", "user_id", "event_id", "event_type", "timestamp_ms",
            "duration_since_last_event", "source", "tab_id", "window_id",
            "full_url", "domain", "path", "query_string", "title",
            "scroll_delta_cumulative", "scroll_depth_last", "scroll_depth_max",
            "scroll_event_count", "tab_active", "visibility_state",
            "chrome_in_foreground", "site_type", "task_hint", "app_name",
            "window_title", "duration", "reaction_time", "dual_task_success",
            "dual_task_error", "missed_response", "mental_demand",
            "physical_demand", "temporal_demand", "performance", "effort",
            "frustration", "stress_self_report", "valence", "arousal"
        ]

    def start_session(self, session_id: str):
        if self.file_handle:
            self.stop_session()
        
        file_path = self.data_dir / f"{session_id}.csv"
        self.file_handle = open(file_path, mode='w', newline='', encoding='utf-8')
        self.writer = csv.DictWriter(self.file_handle, fieldnames=self.fieldnames)
        self.writer.writeheader()
        self.current_file = file_path
        logging.info(f"Started CSV session: {file_path}")
        return str(file_path)

    def write_event(self, event_data: Dict[str, Any]):
        if not self.writer:
            logging.error("Attempted to write event without active session")
            return False
        
        # Map JSON fields to CSV columns if necessary
        row = {}
        for field in self.fieldnames:
            # Handle timestamp mapping
            if field == "timestamp_ms" and "timestamp" in event_data:
                row[field] = event_data["timestamp"]
            else:
                row[field] = event_data.get(field, "")
        
        # Convert booleans to 0/1
        for bool_field in ["tab_active", "chrome_in_foreground"]:
            if bool_field in row and isinstance(row[bool_field], bool):
                row[bool_field] = 1 if row[bool_field] else 0

        self.writer.writerow(row)
        self.file_handle.flush()
        return True

    def write_events(self, events: List[Dict[str, Any]]):
        success_count = 0
        for event in events:
            if self.write_event(event):
                success_count += 1
        return success_count

    def stop_session(self):
        if self.file_handle:
            self.file_handle.close()
            logging.info(f"Stopped CSV session: {self.current_file}")
            self.file_handle = None
            self.writer = None
            self.current_file = None
