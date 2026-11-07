"""无窗口批量导出：处理 samples/ 下所有图片，输出双窗口并排预览图。

用法：
    python tools/preview.py                 # 处理 samples/*.jpg -> out/preview_*.png
    python tools/preview.py a.jpg b.jpg     # 指定文件
"""

from __future__ import annotations

import sys
from pathlib import Path

import cv2

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from linefollow.app import imread_unicode, imwrite_unicode  # noqa: E402
from linefollow.config import Config  # noqa: E402
from linefollow.detector import LineDetector  # noqa: E402
from linefollow.overlay import render_path_canvas, render_source, side_by_side  # noqa: E402


def main() -> int:
    args = sys.argv[1:]
    files = [Path(a) for a in args] if args else sorted((ROOT / "samples").glob("*.jpg"))
    out_dir = ROOT / "out"
    out_dir.mkdir(exist_ok=True)

    cfg = Config()
    det = LineDetector(cfg)
    for path in files:
        img = imread_unicode(path)
        if img is None:
            print(f"skip {path}")
            continue
        d = det.detect(img)
        src = render_source(img, d, cfg, fps=None)
        canvas = render_path_canvas((src.shape[1], src.shape[0]), d, cfg)
        combo = side_by_side(src, canvas)
        max_w = 2400
        if combo.shape[1] > max_w:
            s = max_w / combo.shape[1]
            combo = cv2.resize(combo, (max_w, int(combo.shape[0] * s)), interpolation=cv2.INTER_AREA)
        target = out_dir / f"preview_{path.stem}.png"
        imwrite_unicode(target, combo)
        print(
            f"{path.name}: valid={d.valid} pts={len(d.path)} "
            f"conf={d.confidence:.2f} offset={d.offset_px:+.1f} -> {target}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
