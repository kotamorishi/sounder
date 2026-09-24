"""標準ライブラリだけで行カバレッジを測る。

    python3 tests/coverage_report.py            # 概要
    python3 tests/coverage_report.py --missing   # 未通過の行番号も表示

外部パッケージ（coverage.py）を入れずに済ませるための小さな道具。
sys.settrace で行イベントを数え、バイトコードから取り出した「実行されうる行」と
突き合わせる。スレッド内のコードも threading.settrace で拾う。
"""

from __future__ import annotations

import sys
import threading
import types
import unittest
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PKG = ROOT / "sounder"


def executable_lines(path: Path) -> set[int]:
    """そのファイルで実行されうる行番号（バイトコードに現れる行）。"""
    code = compile(path.read_text("utf-8"), str(path), "exec")
    lines: set[int] = set()
    stack: list[types.CodeType] = [code]
    while stack:
        c = stack.pop()
        for _start, _end, ln in c.co_lines():
            if ln:
                lines.add(ln)
        for const in c.co_consts:
            if isinstance(const, types.CodeType):
                stack.append(const)
    return lines


def main(argv: list[str]) -> int:
    show_missing = "--missing" in argv
    targets = {str(p.resolve()): p for p in sorted(PKG.glob("*.py"))}
    covered: dict[str, set[int]] = defaultdict(set)

    def tracer(frame, event, _arg):
        name = frame.f_code.co_filename
        if event == "call":
            return tracer if name in targets else None
        if event == "line":
            covered[name].add(frame.f_lineno)
        return tracer

    sys.path.insert(0, str(ROOT))
    threading.settrace(tracer)
    sys.settrace(tracer)
    try:
        # 計測開始後に読み込む（モジュール直下の行も数えたいので import はここ）
        suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"), top_level_dir=str(ROOT / "tests"))
        result = unittest.TextTestRunner(verbosity=1, stream=sys.stderr).run(suite)
    finally:
        sys.settrace(None)
        threading.settrace(None)

    print()
    print(f"{'ファイル':<22}{'行数':>6}{'通過':>6}{'未通過':>7}  率")
    print("-" * 52)
    total_all = total_hit = 0
    rows = []
    for key, path in targets.items():
        expected = executable_lines(path)
        hit = expected & covered.get(key, set())
        missing = sorted(expected - hit)
        total_all += len(expected)
        total_hit += len(hit)
        pct = 100.0 * len(hit) / len(expected) if expected else 100.0
        rows.append((path.name, len(expected), len(hit), missing, pct))
    for name, n, h, missing, pct in rows:
        print(f"{name:<22}{n:>6}{h:>6}{len(missing):>7}  {pct:5.1f}%")
        if show_missing and missing:
            print(f"    未通過: {compress(missing)}")
    print("-" * 52)
    pct = 100.0 * total_hit / total_all if total_all else 100.0
    print(f"{'合計':<22}{total_all:>6}{total_hit:>6}{total_all - total_hit:>7}  {pct:5.1f}%")
    return 0 if result.wasSuccessful() else 1


def compress(nums: list[int]) -> str:
    """[1,2,3,7] -> '1-3, 7'"""
    out, start, prev = [], nums[0], nums[0]
    for n in nums[1:]:
        if n == prev + 1:
            prev = n
            continue
        out.append(f"{start}-{prev}" if start != prev else f"{start}")
        start = prev = n
    out.append(f"{start}-{prev}" if start != prev else f"{start}")
    return ", ".join(out)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
