import argparse
import logging
import signal
import sys
import threading
import time
from pathlib import Path
from dotenv import load_dotenv
from server import app as flask_app
from collector import BehaviorCollector

def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="System behavior collector & core server")
    parser.add_argument(
        "--port",
        type=int,
        default=8765,
        help="Port for the core HTTP server.",
    )
    return parser.parse_args()

def main() -> int:
    args = parse_args()
    configure_logging()

    project_root = Path(__file__).resolve().parents[1]
    env_file = project_root / ".env"
    load_dotenv(env_file)

    collector = BehaviorCollector(
        server_url=f"http://localhost:{args.port}",
        poll_interval=0.5,
        emit_interval=30.0,
        merge_flush_threshold=30.0,
    )

    def _handle_signal(_signum: int, _frame: object) -> None:
        logging.info("Shutdown signal received")
        collector.request_stop()
        # Flask doesn't have a simple way to stop from a thread, 
        # but the process will exit when main thread finishes.

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    # Start collector in a background thread
    collector_thread = threading.Thread(target=collector.run_forever, daemon=True)
    collector_thread.start()

    # Start Flask server in the main thread
    flask_app.start_time = time.time()
    logging.info(f"Starting core server on port {args.port}")
    flask_app.run(host='0.0.0.0', port=args.port, debug=False, use_reloader=False)

    return 0

if __name__ == "__main__":
    sys.exit(main())
