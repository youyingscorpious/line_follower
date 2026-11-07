"""主循环：读帧 → 检测 → 两个窗口显示。"""

from __future__ import annotations

import os
import time
from pathlib import Path

import cv2
import numpy as np

from .config import Config
from .detector import Detection, LineDetector
from .overlay import render_path_canvas, render_source

WIN_SOURCE = "Original + Detection"
WIN_PATH = "Path on Black"

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff"}


# --------------------------------------------------------------------- IO
def imread_unicode(path: str | Path) -> np.ndarray | None:
    """cv2.imread 在非 ASCII 路径下会失败，这里用 numpy 兜底。"""
    try:
        buf = np.fromfile(str(path), dtype=np.uint8)
    except OSError:
        return None
    if buf.size == 0:
        return None
    return cv2.imdecode(buf, cv2.IMREAD_COLOR)


def imwrite_unicode(path: str | Path, img: np.ndarray) -> bool:
    ext = os.path.splitext(str(path))[1] or ".png"
    ok, buf = cv2.imencode(ext, img)
    if not ok:
        return False
    buf.tofile(str(path))
    return True


class _Trackbars:
    """调试滑条：拖动即时生效。"""

    def __init__(self, detector: LineDetector, cfg: Config) -> None:
        self.detector = detector
        self.cfg = cfg
        cv2.createTrackbar("thresh", WIN_SOURCE, int(cfg.black_thresh), 255, lambda _v: None)
        cv2.createTrackbar("min_w", WIN_SOURCE, int(cfg.min_line_width), 60, lambda _v: None)
        cv2.createTrackbar("max_w", WIN_SOURCE, int(cfg.max_line_width), 400, lambda _v: None)

    def sync(self) -> None:
        thresh = cv2.getTrackbarPos("thresh", WIN_SOURCE)
        min_w = cv2.getTrackbarPos("min_w", WIN_SOURCE)
        max_w = max(min_w + 1, cv2.getTrackbarPos("max_w", WIN_SOURCE))
        cfg = self.cfg
        if (thresh, min_w, max_w) != (cfg.black_thresh, cfg.min_line_width, cfg.max_line_width):
            self.detector.update(
                black_thresh=thresh, min_line_width=min_w, max_line_width=max_w
            )


def _open_source(source: str):
    """返回 (帧生成器, 是否静态图, 建议帧率, VideoCapture 或 None)。"""
    if source.isdigit():
        cap = cv2.VideoCapture(int(source))
        if not cap.isOpened():
            raise SystemExit(f"打不开摄像头 {source}")
        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        return _cap_frames(cap), False, fps, cap

    path = Path(source)
    if not path.exists():
        raise SystemExit(f"文件不存在: {source}")

    if path.suffix.lower() in IMAGE_EXT:
        img = imread_unicode(path)
        if img is None:
            raise SystemExit(f"读不了图片: {source}")
        return iter([img]), True, 0.0, None

    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise SystemExit(f"打不开视频: {source}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    return _cap_frames(cap), False, fps, cap


def _cap_frames(cap: cv2.VideoCapture):
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        yield frame


# --------------------------------------------------------------- 渲染封装
def _render(frame: np.ndarray, det: Detection, cfg: Config, fps: float | None):
    src = render_source(frame, det, cfg, fps)
    canvas = render_path_canvas((src.shape[1], src.shape[0]), det, cfg)
    return src, canvas


def _create_windows() -> None:
    for win in (WIN_SOURCE, WIN_PATH):
        cv2.namedWindow(win, cv2.WINDOW_NORMAL | cv2.WINDOW_KEEPRATIO)


def _screen_size() -> tuple[int, int] | None:
    try:
        import ctypes

        user32 = ctypes.windll.user32  # type: ignore[attr-defined]
        return int(user32.GetSystemMetrics(0)), int(user32.GetSystemMetrics(1))
    except Exception:
        return None


def _layout_windows(cfg: Config, frame_shape: tuple[int, ...]) -> None:
    """两个窗口并排放，尺寸按屏幕自动缩放，保证同屏可见。"""
    fh, fw = frame_shape[0], frame_shape[1]
    scale = cfg.window_scale
    scr = _screen_size()
    if scr:
        scale = min(scale, 0.47 * scr[0] / fw, 0.84 * scr[1] / fh)
    w = max(320, int(fw * scale))
    h = max(240, int(fh * scale))
    for win in (WIN_SOURCE, WIN_PATH):
        cv2.resizeWindow(win, w, h)
    if scr:
        gap = 16
        cv2.moveWindow(WIN_SOURCE, 8, 48)
        cv2.moveWindow(WIN_PATH, max(gap, scr[0] - w - 8), 48)


def _show(src: np.ndarray, canvas: np.ndarray) -> None:
    cv2.imshow(WIN_SOURCE, src)
    cv2.imshow(WIN_PATH, canvas)


def _save_pair(out_dir: Path, src: np.ndarray, canvas: np.ndarray, prefix: str) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    imwrite_unicode(out_dir / f"{prefix}_src.png", src)
    imwrite_unicode(out_dir / f"{prefix}_path.png", canvas)


# -------------------------------------------------------------------- 主流程
def run(args) -> int:
    cfg = Config(
        proc_width=args.proc_width,
        use_adaptive=args.adaptive,
        black_thresh=args.thresh,
        lookahead_ratio=args.lookahead,
    )
    detector = LineDetector(cfg)
    frames, is_image, fps_src, cap = _open_source(args.source)
    out_dir = Path(args.save_dir)
    gui = not args.no_gui
    is_camera = args.source.isdigit()
    # 视频文件按原帧率播放，摄像头则尽快处理
    delay = 1 if is_camera else max(1, round(1000.0 / max(1.0, fps_src)))

    try:
        if is_image:
            return _run_image(args, cfg, detector, frames, gui, out_dir)

        if gui:
            _create_windows()
        tb = _Trackbars(detector, cfg) if (gui and args.tune) else None

        fps_disp: float | None = None
        prev_t = time.perf_counter()
        count = 0
        paused = False
        sized = False
        src = canvas = None

        while True:
            if paused and gui:
                key = cv2.waitKey(30) & 0xFF
            else:
                try:
                    frame = next(frames)
                except StopIteration:
                    break
                if tb is not None:
                    tb.sync()

                det = detector.detect(frame)
                src, canvas = _render(frame, det, cfg, fps_disp)
                now = time.perf_counter()
                inst = 1.0 / max(1e-6, now - prev_t)
                prev_t = now
                fps_disp = inst if fps_disp is None else 0.85 * fps_disp + 0.15 * inst
                count += 1

                if args.save:
                    out_dir.mkdir(parents=True, exist_ok=True)
                    imwrite_unicode(out_dir / f"frame_{count:05d}_src.jpg", src)
                    imwrite_unicode(out_dir / f"frame_{count:05d}_path.jpg", canvas)

                if gui:
                    if not sized:
                        _layout_windows(cfg, frame.shape)
                        sized = True
                    _show(src, canvas)
                    key = cv2.waitKey(delay) & 0xFF
                else:
                    key = 255

            if key in (ord("q"), 27):
                break
            if key == ord(" "):
                paused = not paused
            if key == ord("s") and src is not None:
                _save_pair(out_dir, src, canvas, "snapshot")
                print(f"[s] 已保存到 {out_dir.resolve()}")

        print(f"处理帧数: {count}")
        return 0
    finally:
        if cap is not None:
            cap.release()
        if gui:
            cv2.destroyAllWindows()


def _run_image(args, cfg: Config, detector: LineDetector, frames, gui: bool, out_dir: Path) -> int:
    frame = next(frames)

    if not gui:
        det = detector.detect(frame)
        src, canvas = _render(frame, det, cfg, None)
        print(
            f"valid={det.valid} points={len(det.path)} conf={det.confidence:.2f} "
            f"offset={det.offset_px:+.1f}px heading={det.heading_deg:+.1f}deg"
        )
        if args.save:
            out_dir.mkdir(parents=True, exist_ok=True)
            imwrite_unicode(out_dir / "result_src.png", src)
            imwrite_unicode(out_dir / "result_path.png", canvas)
            print(f"已保存: {out_dir / 'result_src.png'} , {out_dir / 'result_path.png'}")
        return 0

    _create_windows()
    _layout_windows(cfg, frame.shape)
    tb = _Trackbars(detector, cfg) if args.tune else None

    first = True
    src = canvas = None
    while True:
        if tb is not None:
            tb.sync()
        det = detector.detect(frame)
        src, canvas = _render(frame, det, cfg, None)
        if first:
            print(
                f"valid={det.valid} points={len(det.path)} conf={det.confidence:.2f} "
                f"offset={det.offset_px:+.1f}px heading={det.heading_deg:+.1f}deg"
            )
            if args.save:
                out_dir.mkdir(parents=True, exist_ok=True)
                imwrite_unicode(out_dir / "result_src.png", src)
                imwrite_unicode(out_dir / "result_path.png", canvas)
                print(f"已保存: {out_dir / 'result_src.png'} , {out_dir / 'result_path.png'}")
            first = False
        _show(src, canvas)

        key = cv2.waitKey(30) & 0xFF
        if key in (ord("q"), 27):
            break
        if key == ord("s"):
            _save_pair(out_dir, src, canvas, "snapshot")
            print(f"[s] 已保存到 {out_dir.resolve()}")
    return 0
