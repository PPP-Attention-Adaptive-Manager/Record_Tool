from __future__ import annotations

import logging
import statistics
import threading
import time
from typing import Callable, Optional

LOGGER = logging.getLogger(__name__)

_SAMPLE_INTERVAL = 0.2       # 5 Hz
_WINDOW_SAMPLES = 10          # 10 × 0.2 s = 2 s aggregation window
_CPU_SPIKE_THRESHOLD = 85.0   # percent — flag when mean exceeds this
_RAM_PRESSURE_THRESHOLD = 80.0  # percent


class SystemMetricsCollector:
    """Samples CPU / RAM / network at 5 Hz; emits one aggregated event per 2-second window.

    Event fields:
        timestamp, cpu_mean, cpu_std, cpu_spike_flag,
        ram_mean, memory_pressure_flag,
        bytes_in, bytes_out, network_rate_bps
    """

    def __init__(self, on_event: Callable[[dict], None], enabled: bool = True):
        self._on_event = on_event
        self._enabled = enabled
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

    def start(self) -> None:
        if not self._enabled:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run, name="system-metrics", daemon=True
        )
        self._thread.start()
        LOGGER.info("System metrics collector started (window=%ds, sample=%.1fHz)",
                    int(_SAMPLE_INTERVAL * _WINDOW_SAMPLES), 1.0 / _SAMPLE_INTERVAL)

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=3.0)
            self._thread = None
        LOGGER.info("System metrics collector stopped")

    def _run(self) -> None:
        try:
            import psutil
        except ImportError:
            LOGGER.warning("psutil not available — system metrics disabled")
            return

        cpu_buf: list[float] = []
        ram_buf: list[float] = []
        prev_net = psutil.net_io_counters()
        prev_net_time = time.time()

        while not self._stop_event.is_set():
            cpu_buf.append(psutil.cpu_percent(interval=None))
            ram_buf.append(psutil.virtual_memory().percent)

            if len(cpu_buf) >= _WINDOW_SAMPLES:
                now = time.time()
                net = psutil.net_io_counters()
                elapsed = max(now - prev_net_time, 0.001)

                bytes_in = max(0, net.bytes_recv - prev_net.bytes_recv)
                bytes_out = max(0, net.bytes_sent - prev_net.bytes_sent)
                network_rate = (bytes_in + bytes_out) / elapsed

                cpu_mean = statistics.mean(cpu_buf)
                cpu_std = statistics.pstdev(cpu_buf)
                ram_mean = statistics.mean(ram_buf)

                self._on_event({
                    "timestamp": now,
                    "cpu_mean": round(cpu_mean, 2),
                    "cpu_std": round(cpu_std, 2),
                    "cpu_spike_flag": int(cpu_mean > _CPU_SPIKE_THRESHOLD),
                    "ram_mean": round(ram_mean, 2),
                    "memory_pressure_flag": int(ram_mean > _RAM_PRESSURE_THRESHOLD),
                    "bytes_in": bytes_in,
                    "bytes_out": bytes_out,
                    "network_rate_bps": round(network_rate, 0),
                })

                cpu_buf.clear()
                ram_buf.clear()
                prev_net = net
                prev_net_time = now

            self._stop_event.wait(_SAMPLE_INTERVAL)
