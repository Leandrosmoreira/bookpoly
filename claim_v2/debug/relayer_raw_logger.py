"""Logger JSONL para respostas cruas do relayer (sem secrets)."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class RelayerRawLogger:
    """Persiste eventos de debug do relayer em JSONL."""

    def __init__(self, logs_dir: Path, run_id: str, enabled: bool = False):
        self.enabled = enabled
        self.path = logs_dir / f"relayer_raw_{run_id}.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _sanitize(obj: Any) -> Any:
        """Sanitiza payload para serializar com segurança."""
        if obj is None:
            return None
        if isinstance(obj, (str, int, float, bool)):
            return obj
        if isinstance(obj, dict):
            out = {}
            for k, v in obj.items():
                lk = str(k).lower()
                if any(x in lk for x in ("secret", "signature", "passphrase", "api_key", "private")):
                    out[k] = "***redacted***"
                else:
                    out[k] = RelayerRawLogger._sanitize(v)
            return out
        if isinstance(obj, (list, tuple)):
            return [RelayerRawLogger._sanitize(v) for v in obj]
        try:
            # Dataclass/object simples
            d = getattr(obj, "__dict__", None)
            if isinstance(d, dict):
                return RelayerRawLogger._sanitize(d)
        except Exception:
            pass
        return str(obj)

    def log(self, event: str, payload: Any):
        if not self.enabled:
            return
        row = {
            "ts_iso": datetime.now(timezone.utc).isoformat(),
            "event": event,
            "payload": self._sanitize(payload),
        }
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=True) + "\n")

