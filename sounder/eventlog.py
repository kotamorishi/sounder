"""実行ログ。直近はメモリに、全件はファイルに追記する。"""

from __future__ import annotations

import threading
from collections import deque
from datetime import datetime
from pathlib import Path

MAX_BYTES = 1_000_000


class EventLog:
    def __init__(self, path: Path, keep: int = 300) -> None:
        self.path = path
        self._buf: deque[dict] = deque(maxlen=keep)
        self._lock = threading.Lock()

    def __call__(self, level: str, message: str, **extra) -> None:
        entry = {"ts": datetime.now().isoformat(timespec="seconds"),
                 "level": level, "message": message, **extra}
        with self._lock:
            self._buf.append(entry)
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                if self.path.exists() and self.path.stat().st_size > MAX_BYTES:
                    self.path.replace(self.path.with_suffix(".1.log"))
                with self.path.open("a", encoding="utf-8") as f:
                    f.write(f"{entry['ts']}\t{level}\t{message}\n")
            except OSError:
                pass
        print(f"[sounder] {entry['ts']} {level}: {message}", flush=True)

    def recent(self, limit: int = 100) -> list[dict]:
        with self._lock:
            return list(self._buf)[-limit:][::-1]
