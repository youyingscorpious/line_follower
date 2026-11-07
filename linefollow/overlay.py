"""两个窗口的绘制。

窗口 1「Original + Detection」：原图上叠加识别结果（走廊 / 中心线 / 前瞻点 / HUD）。
窗口 2「Path on Black」      ：纯黑画布，只把识别到的路径画出来。
"""

from __future__ import annotations

import cv2
import numpy as np

from .config import Config
from .detector import Detection

GREEN = (80, 255, 120)
CYAN = (255, 220, 60)
RED = (60, 60, 255)
AMBER = (40, 200, 255)
WHITE = (240, 240, 240)


def _pt(arr: np.ndarray, k: float) -> np.ndarray:
    return np.round(arr.astype(np.float32) * k).astype(np.int32)


def _alpha_rect(img: np.ndarray, p1, p2, color, alpha: float = 0.55) -> None:
    x1, y1 = max(0, int(p1[0])), max(0, int(p1[1]))
    x2, y2 = min(img.shape[1], int(p2[0])), min(img.shape[0], int(p2[1]))
    if x2 <= x1 or y2 <= y1:
        return
    roi = img[y1:y2, x1:x2]
    panel = np.full_like(roi, color, dtype=np.uint8)
    cv2.addWeighted(panel, alpha, roi, 1.0 - alpha, 0, roi)


def _hud_lines(det: Detection, cfg: Config, fps: float | None) -> list[str]:
    if not det.valid:
        return ["LINE: LOST", "confidence 0.00"]
    lines = [
        f"LINE: TRACKING  conf={det.confidence:.2f}",
        f"offset={det.offset_px:+.1f}px ({det.offset_ratio:+.2f})",
        f"heading={det.heading_deg:+.1f} deg",
    ]
    if fps is not None and cfg.show_fps:
        lines.append(f"fps={fps:5.1f}")
    return lines


def draw_corridor(canvas: np.ndarray, det: Detection, k: float, color, alpha: float) -> None:
    """在左右边缘之间填充半透明走廊。"""
    if det.left.shape[0] < 2 or det.right.shape[0] < 2:
        return
    left = _pt(det.left, k)
    right = _pt(det.right, k)[::-1]
    pts = np.vstack([left, right])
    overlay = canvas.copy()
    cv2.fillPoly(overlay, [pts], color)
    cv2.addWeighted(overlay, alpha, canvas, 1.0 - alpha, 0, canvas)


def render_source(
    frame: np.ndarray,
    det: Detection,
    cfg: Config | None = None,
    fps: float | None = None,
) -> np.ndarray:
    """窗口 1：原图 + 识别叠加。"""
    cfg = cfg or Config()
    out = frame.copy()
    h, w = out.shape[:2]
    k = 1.0 / det.scale if det.scale > 0 else 1.0
    lw = max(1, round(w / 480))

    if det.valid:
        if cfg.draw_corridor:
            draw_corridor(out, det, k, GREEN, 0.16)

        path = _pt(det.path, k)
        if len(path) >= 2:
            cv2.polylines(out, [path], False, (0, 0, 0), lw * 4, cv2.LINE_AA)
            cv2.polylines(out, [path], False, RED, lw * 2, cv2.LINE_AA)

        for arr, color in ((det.left, CYAN), (det.right, CYAN)):
            for x, y in _pt(arr, k)[:: max(1, len(arr) // 24)]:
                cv2.circle(out, (int(x), int(y)), max(1, lw), color, -1, cv2.LINE_AA)

        if det.lookahead:
            lx, ly = int(det.lookahead[0] * k), int(det.lookahead[1] * k)
            r = max(4, round(w / 110))
            cv2.circle(out, (lx, ly), r, AMBER, lw * 2, cv2.LINE_AA)
            cv2.line(out, (lx - r * 2, ly), (lx + r * 2, ly), AMBER, lw, cv2.LINE_AA)
            cv2.line(out, (lx, ly - r * 2), (lx, ly + r * 2), AMBER, lw, cv2.LINE_AA)

        # 车头 -> 前瞻点 的方向箭头
        bx, by = det.path[0]
        if det.lookahead:
            cv2.arrowedLine(
                out,
                (int(bx * k), int(by * k)),
                (int(det.lookahead[0] * k), int(det.lookahead[1] * k)),
                AMBER,
                lw,
                cv2.LINE_AA,
                tipLength=0.06,
            )
        status_color = GREEN
    else:
        status_color = RED

    if not cfg.show_hud:
        return out

    # ---- HUD ----
    fs = max(0.4, w / 1500.0)
    lines = _hud_lines(det, cfg, fps)
    thick = max(1, round(fs * 2))
    widths = [cv2.getTextSize(t, cv2.FONT_HERSHEY_SIMPLEX, fs, thick)[0][0] for t in lines]
    pad = max(8, int(12 * fs * 1.8))
    line_h = int(fs * 34)
    box_w = max(widths) + pad * 2
    box_h = int(len(lines) * line_h + pad * 1.8)
    _alpha_rect(out, (0, 0), (box_w, box_h), (12, 12, 12), 0.72)
    cv2.line(out, (0, box_h), (box_w, box_h), status_color, max(1, lw), cv2.LINE_AA)

    if cfg.show_border:
        cv2.rectangle(out, (0, 0), (w - 1, h - 1), status_color, max(2, min(6, lw * 2)))

    y = pad + int(fs * 20)
    for i, text in enumerate(lines):
        color = status_color if i == 0 else WHITE
        cv2.putText(out, text, (pad, y), cv2.FONT_HERSHEY_SIMPLEX, fs, (0, 0, 0), thick + 2, cv2.LINE_AA)
        cv2.putText(out, text, (pad, y), cv2.FONT_HERSHEY_SIMPLEX, fs, color, thick, cv2.LINE_AA)
        y += line_h
    return out


def render_path_canvas(
    size: tuple[int, int],
    det: Detection,
    cfg: Config | None = None,
) -> np.ndarray:
    """窗口 2：纯黑画布上只画识别到的路径（不画任何文字）。"""
    cfg = cfg or Config()
    w, h = size
    canvas = np.zeros((h, w, 3), np.uint8)
    k = w / max(1, det.mask.shape[1])  # proc -> 画布
    lw = max(2, round(w / 320))

    if not det.valid or det.path.shape[0] < 2:
        return canvas

    # 走廊（暗绿）
    if cfg.draw_corridor:
        draw_corridor(canvas, det, k, (0, 60, 30), 0.75)

    path = _pt(det.path, k)

    
    # 路线
    cv2.polylines(canvas, [path], False, (0, 170, 70), lw * 3, cv2.LINE_AA)
     

    # 左右边界轨道
    for arr in (det.left, det.right):
        rail = _pt(arr, k)
        if len(rail) >= 2:
            cv2.polylines(canvas, [rail], False, (40, 110, 90), max(1, lw // 2), cv2.LINE_AA)

    # 起点（车头）/ 终点
    sx, sy = path[min(6, len(path) - 1)]
    cv2.circle(canvas, (int(sx), int(sy)), lw * 5, (255, 255, 255), -1, cv2.LINE_AA)
    cv2.circle(canvas, (int(sx), int(sy)), lw * 5, (0, 255, 0), lw, cv2.LINE_AA)
    ex, ey = path[-1]
    cv2.drawMarker(canvas, (int(ex), int(ey)), CYAN, cv2.MARKER_DIAMOND, lw * 10, max(1, lw // 2))

    return canvas


def side_by_side(left: np.ndarray, right: np.ndarray, gap: int = 12) -> np.ndarray:
    """把两个窗口并排拼成一张图（导出预览用）。"""
    h = max(left.shape[0], right.shape[0])
    wl, wr = left.shape[1], right.shape[1]

    def _fit(img: np.ndarray) -> np.ndarray:
        canvas = np.zeros((h, img.shape[1], 3), np.uint8)
        canvas[: img.shape[0]] = img
        return canvas

    out = np.zeros((h, wl + wr + gap, 3), np.uint8)
    out[:, :wl] = _fit(left)
    out[:, wl + gap :] = _fit(right)
    return out
