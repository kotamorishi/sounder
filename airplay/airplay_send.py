"""AirPlay（HomePod など）へ音を送る補助。.venv-airplay の Python（pyatv 入り）で動かす。

    airplay_send.py scan
        → 標準出力に JSON: [{"id", "name", "model", "address"}, ...]
    airplay_send.py play --device ID[@ADDR] [--device ...] [--volume 0〜1] -- ファイル...
        → すべての機器につないだところで標準出力に 1 行 {"connected": [id...], "failed": [id...]} を出し、
          つながった機器で一斉にファイルを順に鳴らす。鳴り終わったら終了コード 0
          終了コード 2 = どの機器にもつながらない（何も鳴っていない）。sounder はこの Mac で代わりに鳴らす

sounder 本体は標準ライブラリだけで動かすため、pyatv はこの別プロセスに閉じ込める。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

import pyatv
from pyatv.const import Protocol

NOT_FOUND = 2


async def scan(timeout: int) -> list[dict]:
    loop = asyncio.get_running_loop()
    found = await pyatv.scan(loop, timeout=timeout, protocol={Protocol.RAOP})
    out = []
    for conf in found:
        info = conf.device_info
        out.append({"id": conf.identifier, "name": conf.name, "address": str(conf.address),
                    "model": str(info.model_str or "")})
    return sorted(out, key=lambda d: d["name"])


async def find(ident: str, address: str | None):
    loop = asyncio.get_running_loop()
    if address:   # 前に見えたアドレスを先に見る（速い）。変わっていたら全体から探す
        found = await pyatv.scan(loop, timeout=3, hosts=[address], protocol={Protocol.RAOP})
        found = [c for c in found if c.identifier == ident or ident in c.all_identifiers]
        if found:
            return found[0]
    found = await pyatv.scan(loop, timeout=5, identifier=ident, protocol={Protocol.RAOP})
    return found[0] if found else None


async def connect(ident: str, address: str | None, volume: float | None):
    conf = await find(ident, address)
    if conf is None:
        print(f"AirPlay の機器が見つかりません: {ident}", file=sys.stderr)
        return None
    try:
        atv = await pyatv.connect(conf, asyncio.get_running_loop())
    except Exception as exc:  # noqa: BLE001
        print(f"つながりませんでした: {conf.name}: {exc!r}", file=sys.stderr)
        return None
    if volume is not None:
        try:
            await atv.audio.set_volume(max(0.0, min(100.0, volume * 100)))
        except Exception:  # noqa: BLE001  音量を変えられない機器でも鳴らす
            pass
    return atv


async def stream(atv, files: list[str]) -> bool:
    try:
        for f in files:
            await atv.stream.stream_file(f)
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"送れませんでした: {exc!r}", file=sys.stderr)
        return False
    finally:
        atv.close()


async def play(devices: list[tuple[str, str | None]], volume: float | None, files: list[str]) -> int:
    atvs = await asyncio.gather(*(connect(i, a, volume) for i, a in devices))
    ok = [(ident, atv) for (ident, _a), atv in zip(devices, atvs) if atv]
    print(json.dumps({"connected": [i for i, _ in ok],
                      "failed": [i for (i, _a), atv in zip(devices, atvs) if not atv]}), flush=True)
    if not ok:
        return NOT_FOUND
    done = await asyncio.gather(*(stream(atv, files) for _i, atv in ok))
    return 0 if all(done) else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("scan")
    s.add_argument("--timeout", type=int, default=4)
    p = sub.add_parser("play")
    p.add_argument("--device", action="append", required=True, help="ID か ID@アドレス")
    p.add_argument("--volume", type=float)
    p.add_argument("files", nargs="+")
    a = ap.parse_args()
    if a.cmd == "scan":
        print(json.dumps(asyncio.run(scan(a.timeout)), ensure_ascii=False))
        return 0
    devices = [(d.split("@", 1)[0], (d.split("@", 1)[1:] or [None])[0]) for d in a.device]
    return asyncio.run(play(devices, a.volume, a.files))


if __name__ == "__main__":
    sys.exit(main())
