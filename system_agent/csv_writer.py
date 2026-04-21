from __future__ import annotations

import csv
import threading
from pathlib import Path
from typing import Iterable

from models import CSV_COLUMNS, UnifiedEvent


class CsvEventWriter:
    """Append-only CSV writer shared by the API server and collector threads."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._ensure_header()

    def append_event(self, event: UnifiedEvent) -> None:
        self.append_rows([event.to_csv_row()])

    def append_rows(self, rows: Iterable[dict[str, object]]) -> None:
        with self._lock:
            with self.path.open("a", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS)
                for row in rows:
                    writer.writerow({column: row.get(column) for column in CSV_COLUMNS})

    def _ensure_header(self) -> None:
        if self.path.exists() and self.path.stat().st_size > 0:
            return
        with self._lock:
            if self.path.exists() and self.path.stat().st_size > 0:
                return
            with self.path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS)
                writer.writeheader()
