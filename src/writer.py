import os
import json
import logging
from datetime import datetime, timezone
from typing import IO

log = logging.getLogger(__name__)


class Writer:
    """Writes JSONL rows to per-market, per-day files with daily rotation."""

    def __init__(self, base_dir: str):
        self.base_dir = base_dir
        self._handles: dict[str, IO] = {}
        self._current_date: str = ""
        os.makedirs(base_dir, exist_ok=True)

    def _today_utc(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def _rotate_if_needed(self):
        """Close old file handles if the UTC date has changed."""
        today = self._today_utc()
        if today != self._current_date:
            if self._current_date:
                log.info(f"Day rotation: {self._current_date} -> {today}")
            self.close_all()
            self._current_date = today

    def _get_handle(self, market_label: str) -> IO:
        """Get or open file handle for a market on the current day."""
        self._rotate_if_needed()
        key = f"{market_label}_{self._current_date}"
        if key not in self._handles:
            filename = f"{market_label}_{self._current_date}.jsonl"
            filepath = os.path.join(self.base_dir, filename)
            self._handles[key] = open(filepath, "a", encoding="utf-8")
            log.info(f"Opened {filepath}")
        return self._handles[key]

    def write(self, market_label: str, row: dict):
        """Append a JSON row to the appropriate file and flush."""
        f = self._get_handle(market_label)
        line = json.dumps(row, separators=(",", ":"), ensure_ascii=False)
        f.write(line + "\n")
        f.flush()

    def close_all(self):
        """Close all open file handles."""
        for key, f in self._handles.items():
            try:
                f.close()
            except Exception as e:
                log.error(f"Error closing {key}: {e}")
        self._handles.clear()
