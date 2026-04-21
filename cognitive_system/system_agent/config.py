import os
import uuid
from dataclasses import dataclass, field

MODE_EXPERIMENTAL = "experimental"
MODE_PRODUCTION = "production"


@dataclass
class Config:
    # Session
    mode: str = MODE_EXPERIMENTAL
    session_duration_minutes: int = 30
    device_id: str = field(
        default_factory=lambda: os.environ.get(
            "DEVICE_ID", f"device_{uuid.uuid4().hex[:8]}"
        )
    )

    # Servers
    websocket_host: str = "localhost"
    websocket_port: int = 8765
    http_host: str = "localhost"
    http_port: int = 8080

    # InfluxDB
    influxdb_url: str = os.environ.get("INFLUXDB_URL", "http://localhost:8086")
    influxdb_token: str = os.environ.get("INFLUXDB_TOKEN", "my-super-secret-token")
    influxdb_org: str = os.environ.get("INFLUXDB_ORG", "cognitive_lab")
    influxdb_behavior_bucket: str = "behavior_bucket"
    influxdb_keyboard_bucket: str = "keyboard_bucket"
    influxdb_mouse_bucket: str = "mouse_bucket"

    # Data storage root (sibling data/ folder)
    data_dir: str = field(
        default_factory=lambda: os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data"
        )
    )

    # Timing
    session_broadcast_interval: float = 1.0   # seconds between UI updates
    mouse_aggregation_interval: float = 0.1   # 10 Hz mouse sampling


config = Config()
