"""无窗口自检：确认环境可用、三张样图都能稳定出线。

    python tools/selftest.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from linefollow.app import imread_unicode  # noqa: E402
from linefollow.config import Config  # noqa: E402
from linefollow.detector import LineDetector  # noqa: E402


def main() -> int:
    cfg = Config()
    det = LineDetector(cfg)
    failures = 0

    # 1) 三张现场照片都应识别成功
    for path in sorted((ROOT / "samples").glob("*.jpg")):
        img = imread_unicode(path)
        assert img is not None, path
        t0 = time.perf_counter()
        res = det.detect(img)
        ms = (time.perf_counter() - t0) * 1000
        span = (res.path[0, 1] - res.path[-1, 1]) if len(res.path) else 0
        ok = res.valid and len(res.path) > 50 and span > res.mask.shape[0] * 0.4
        print(
            f"[{'OK ' if ok else 'FAIL'}] {path.name:14s} valid={res.valid} "
            f"pts={len(res.path):4d} span={span:6.0f}px conf={res.confidence:.2f} "
            f"offset={res.offset_px:+7.1f} {ms:5.1f}ms"
        )
        failures += 0 if ok else 1

    # 2) 没有线的画面必须判为丢线
    blank = np.full((480, 640, 3), 200, np.uint8)
    res = det.detect(blank)
    ok = not res.valid
    print(f"[{'OK ' if ok else 'FAIL'}] 空白画面        valid={res.valid} (期望 False)")
    failures += 0 if ok else 1

    # 3) 纯黑画面（整块黑）也不能误判
    black = np.zeros((480, 640, 3), np.uint8)
    res = det.detect(black)
    ok = not res.valid
    print(f"[{'OK ' if ok else 'FAIL'}] 全黑画面        valid={res.valid} (期望 False)")
    failures += 0 if ok else 1

    print("全部通过" if failures == 0 else f"{failures} 项失败")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
