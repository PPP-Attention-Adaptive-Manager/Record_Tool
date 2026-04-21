from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import threading
from pathlib import Path

from dotenv import load_dotenv
from werkzeug.serving import make_server

from collector import BehaviorCollector
from influx_client import InfluxBatchClient
from keyboard_collector import KeyboardCollector
from mouse_collector import MouseCollector
from server import SessionController, create_app


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Experiment data-collection core")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    return parser.parse_args()


def optional_influx_client() -> InfluxBatchClient | None:
    url = os.getenv("INFLUX_URL", "").strip()
    token = os.getenv("INFLUX_TOKEN", "").strip()
    org = os.getenv("INFLUX_ORG", "").strip()
    bucket = os.getenv("INFLUX_BUCKET", "").strip()
    if not all([url, token, org, bucket]):
        logging.info("InfluxDB sink disabled; missing one or more INFLUX_* variables")
        return None

    return InfluxBatchClient(
        url=url,
        token=token,
        org=org,
        bucket=bucket,
        batch_size=100,
        flush_interval=3.0,
        max_retries=3,
        request_timeout=10.0,
    )


def main() -> int:
    args = parse_args()
    configure_logging()

    project_root = Path(__file__).resolve().parents[1]
    load_dotenv(project_root / ".env")

    influx_client = optional_influx_client()
    if influx_client is not None:
        influx_client.start()

    controller = SessionController(
        data_dir=project_root / "data",
        schema_path=Path(__file__).resolve().parent / "schemas" / "event_schema.json",
        influx_client=influx_client,
    )
    collector = BehaviorCollector(controller=controller)
    keyboard_collector = KeyboardCollector(controller=controller)
    mouse_collector = MouseCollector(controller=controller)

    collector_thread = threading.Thread(
        target=collector.run_forever,
        name="system-event-collector",
        daemon=True,
    )
    collector_thread.start()
    keyboard_collector.start()
    mouse_collector.start()

    shutdown_requested = threading.Event()
    server_ref: dict[str, object] = {}

    def request_shutdown() -> None:
        if shutdown_requested.is_set():
            return
        logging.info("Shutdown signal received")
        shutdown_requested.set()
        collector.request_stop()
        http_server = server_ref.get("server")
        if http_server is not None:
            http_server.shutdown()

    signal.signal(signal.SIGINT, lambda _signum, _frame: request_shutdown())
    signal.signal(signal.SIGTERM, lambda _signum, _frame: request_shutdown())

    app = create_app(controller, shutdown_callback=request_shutdown)
    http_server = make_server(args.host, args.port, app, threaded=True)
    server_ref["server"] = http_server

    logging.info("Core server listening on http://%s:%s", args.host, args.port)
    try:
        http_server.serve_forever()
    finally:
        request_shutdown()
        keyboard_collector.stop()
        mouse_collector.stop()
        collector_thread.join(timeout=2.0)
        if influx_client is not None:
            influx_client.stop()
        http_server.server_close()
        logging.info("Core shutdown complete")

    return 0


if __name__ == "__main__":
    sys.exit(main())
