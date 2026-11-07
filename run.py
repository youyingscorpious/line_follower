#!/usr/bin/env python
"""巡线程序入口。

例：
    python run.py --image samples/sample1.jpg
    python run.py --video track.mp4
    python run.py --camera 0
    python run.py --image samples/sample1.jpg --save out      # 存结果图（不开窗口）
"""

from __future__ import annotations

import argparse

try:
    from linefollow.app import run
except ModuleNotFoundError as exc:  # 没装 opencv
    raise SystemExit(
        f"缺少依赖 {exc.name}。\n"
        "  1) 用项目自带的虚拟环境： .venv\\Scripts\\python.exe run.py --image samples/sample1.jpg\n"
        "  2) 或者先安装：            pip install -r requirements.txt"
    )


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="OpenCV 黑胶带巡线（双窗口）")
    src = p.add_mutually_exclusive_group()
    src.add_argument("--image", help="输入图片路径")
    src.add_argument("--video", help="输入视频路径")
    src.add_argument("--camera", type=int, help="摄像头编号，默认 0")
    p.add_argument("--proc-width", type=int, default=960, help="处理分辨率宽度，0=原图 (默认 960)")
    p.add_argument("--thresh", type=int, default=85, help="黑色阈值 0-255 (默认 85)")
    p.add_argument("--adaptive", action="store_true", help="叠加自适应阈值（光照不均时用）")
    p.add_argument("--lookahead", type=float, default=0.30, help="前瞻点高度比例 (默认 0.30)")
    p.add_argument("--tune", action="store_true", help="显示调参滑条")
    p.add_argument("--save", action="store_true", help="保存结果图/帧")
    p.add_argument("--save-dir", default="out", help="保存目录 (默认 out)")
    p.add_argument("--no-gui", action="store_true", help="不创建窗口（配合 --save）")
    return p


def main() -> int:
    args = build_parser().parse_args()
    if not (args.image or args.video or args.camera is not None):
        args.camera = 0
    args.source = args.image or args.video or str(args.camera)
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
