"""AirPlay（HomePod など）で鳴らす（オプション）。

pyatv は標準ライブラリではないので、別の Python 環境（.venv-airplay、scripts/install-airplay.sh）に入れ、
補助スクリプト airplay/airplay_send.py をプロセスとして呼ぶ。入っていなければ「この Mac」だけが選べる。

予定の outputs（[{"id", "name", "address"}, ...]。"mac" はこの Mac）に機器を入れると、再生層（player.py）がこれで送る。
つながらなければ、これまでどおりこの Mac で鳴らす。
"""

from __future__ import annotations

import json
import subprocess
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "airplay" / "airplay_send.py"
PYTHON = ROOT / ".venv-airplay" / "bin" / "python"
SCAN_KEEP = 300.0      # 機器の一覧を何秒取っておくか
NOT_FOUND = 2          # 補助スクリプトの「見つからない・つながらない」


class AirPlay:
    def __init__(self, python: Path = PYTHON, script: Path = SCRIPT, *, log=None) -> None:
        self.python = Path(python)
        self.script = Path(script)
        self._log = log or (lambda *a, **k: None)
        self._lock = threading.Lock()
        self._devices: list[dict] = []
        self._scanned = 0.0

    def available(self) -> bool:
        return self.python.is_file() and self.script.is_file()

    def devices(self, refresh: bool = False) -> list[dict]:
        """家の中の AirPlay の機器（数秒かかるので、しばらく取っておく）。"""
        if not self.available():
            return []
        with self._lock:
            if not refresh and self._devices and time.monotonic() - self._scanned < SCAN_KEEP:
                return list(self._devices)
        try:
            r = subprocess.run([str(self.python), str(self.script), "scan"],
                               capture_output=True, text=True, timeout=30)
            found = json.loads(r.stdout or "[]")
        except (OSError, subprocess.SubprocessError, ValueError) as exc:
            self._log("error", f"AirPlay の機器を探せませんでした: {exc}")
            return list(self._devices)
        with self._lock:
            self._devices = [{k: str(d.get(k) or "") for k in ("id", "name", "model", "address")}
                             for d in found if d.get("id")]
            self._scanned = time.monotonic()
            return list(self._devices)

    def argv(self, outputs: list[dict], volume: float | None, files: list[str]) -> list[str]:
        """files を outputs の機器すべてで一斉に鳴らすコマンド。"""
        known = {d["id"]: d for d in self._devices}
        argv = [str(self.python), str(self.script), "play"]
        for o in outputs:
            address = (known.get(o["id"]) or {}).get("address") or o.get("address") or ""
            argv += ["--device", f"{o['id']}@{address}" if address else o["id"]]
        if volume is not None:
            argv += ["--volume", f"{float(volume):.3f}"]
        return argv + ["--"] + [str(f) for f in files]
