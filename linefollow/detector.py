"""黑胶带检测 + 扫描线中心线提取。

思路（自下而上，和机器人实际巡线一致）：

1. 灰度 + 模糊 + 阈值（+ 形态学）得到黑色区域掩膜；
2. 从图像底部逐行向上扫描，每一行找"宽度合理"的黑色像素段；
3. 用上一行的中心点做连续性约束，挑最近的那一段
4. 断口做线性插值，再做滑动平均，得到平滑有序的中心线；
5. 由前瞻点算出横向偏差 / 航向角，可直接喂给下层控制器。
"""

from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np

from .config import Config


@dataclass
class Detection:
    """一帧的检测结果（坐标都在处理尺寸 proc 空间里）。"""

    mask: np.ndarray                                   # 黑线掩膜 (H, W) uint8
    path: np.ndarray = field(default_factory=lambda: np.empty((0, 2), np.float32))
    left: np.ndarray = field(default_factory=lambda: np.empty((0, 2), np.float32))
    right: np.ndarray = field(default_factory=lambda: np.empty((0, 2), np.float32))
    lookahead: tuple[int, int] | None = None           # 前瞻点
    offset_px: float = 0.0                             # 前瞻点相对画面中心的横向偏差
    offset_ratio: float = 0.0                          # 归一化偏差，-1(最左) .. +1(最右)
    heading_deg: float = 0.0                           # 车头相对路径方向的偏角
    confidence: float = 0.0                            # 0..1，路径完整程度
    valid: bool = False
    scale: float = 1.0                                 # proc 尺寸 / 原图尺寸


def _runs(row_bool: np.ndarray) -> list[tuple[int, int]]:
    """把一行布尔数组切成连续 True 段，返回 [(start, end), ...]（闭区间）。"""
    idx = np.flatnonzero(row_bool)
    if idx.size == 0:
        return []
    breaks = np.flatnonzero(np.diff(idx) > 1)
    starts = np.concatenate(([idx[0]], idx[breaks + 1]))
    ends = np.concatenate((idx[breaks], [idx[-1]]))
    return list(zip(starts.tolist(), ends.tolist()))


def _odd(k: int) -> int:
    return max(1, k | 1)


class LineDetector:
    """有状态的检测器（保存阈值、形态学核等，避免每帧重建）。"""

    def __init__(self, cfg: Config | None = None) -> None:
        self.cfg = cfg or Config()
        self._rebuild()

    # ------------------------------------------------------------------ 工具
    def _rebuild(self) -> None:
        cfg = self.cfg
        self._open_k = cv2.getStructuringElement(
            cv2.MORPH_ELLIPSE, (_odd(cfg.open_ksize), _odd(cfg.open_ksize))
        )
        self._close_k = cv2.getStructuringElement(
            cv2.MORPH_ELLIPSE, (_odd(cfg.close_ksize), _odd(cfg.close_ksize))
        )

    def update(self, **kwargs) -> None:
        """运行中修改参数（GUI 滑条会用到）。"""
        for k, v in kwargs.items():
            if not hasattr(self.cfg, k):
                raise AttributeError(k)
            setattr(self.cfg, k, v)
        self._rebuild()

    # ------------------------------------------------------------ 图像预处理
    def _to_proc(self, frame: np.ndarray) -> tuple[np.ndarray, float]:
        """等比例缩放到 proc_width，返回 (处理图, 缩放系数)。"""
        h, w = frame.shape[:2]
        target = self.cfg.proc_width
        if not target or w <= target:
            return frame, 1.0
        scale = target / float(w)
        resized = cv2.resize(
            frame, (target, max(1, int(round(h * scale)))), interpolation=cv2.INTER_AREA
        )
        return resized, scale

    def build_mask(self, bgr: np.ndarray) -> np.ndarray:
        cfg = self.cfg
        gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
        if cfg.blur_ksize > 1:
            gray = cv2.GaussianBlur(gray, (_odd(cfg.blur_ksize), _odd(cfg.blur_ksize)), 0)

        _, mask = cv2.threshold(gray, cfg.black_thresh, 255, cv2.THRESH_BINARY_INV)
        if cfg.use_adaptive:
            block = _odd(cfg.adaptive_block)
            adaptive = cv2.adaptiveThreshold(
                gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY_INV, block, cfg.adaptive_c
            )
            # 只保留"既绝对够黑、又比邻域暗"的像素，抑制阴影与反光
            mask = cv2.bitwise_and(mask, adaptive)

        if cfg.open_ksize > 1:
            mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, self._open_k)
        if cfg.close_ksize > 1:
            mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, self._close_k)
        return mask

    # ------------------------------------------------------------ 扫描线追踪
    def _trace(self, mask: np.ndarray) -> tuple[list[tuple[float, int]], list[tuple[int, int]], list[tuple[int, int]]]:
        """自下而上追踪中心点，返回 (中心点, 左边缘, 右边缘)。"""
        cfg = self.cfg
        h, w = mask.shape[:2]
        step = max(1, int(cfg.row_step))
        min_w = max(1, int(cfg.min_line_width))
        max_w = max(min_w, int(cfg.max_line_width))
        max_jump = max(4.0, cfg.max_jump_ratio * w)
        max_gap = max(step, int(cfg.max_gap_ratio * h))
        start_floor = int(h * (1.0 - cfg.start_search_ratio))

        centers: list[tuple[float, int]] = []
        lefts: list[tuple[int, int]] = []
        rights: list[tuple[int, int]] = []
        prev: float | None = None
        miss = 0

        for y in range(h - 1, -1, -step):
            row = mask[y]
            runs = [(s, e) for s, e in _runs(row) if min_w <= (e - s + 1) <= max_w]

            pick: tuple[int, int] | None = None
            if runs:
                if prev is None:
                    # 起点：取最靠近画面中线的候选段
                    pick = min(runs, key=lambda r: abs((r[0] + r[1]) * 0.5 - w * 0.5))
                else:
                    best = min(runs, key=lambda r: abs((r[0] + r[1]) * 0.5 - prev))
                    if abs((best[0] + best[1]) * 0.5 - prev) <= max_jump:
                        pick = best

            if pick is None:
                if prev is None:
                    if y < start_floor:   # 只在图底部这段里找起点
                        break
                    continue              # 还没找到起点，继续往上找
                miss += step
                if miss > max_gap:        # 断线太长，认为路径结束
                    break
                continue

            cx = (pick[0] + pick[1]) * 0.5
            centers.append((cx, y))
            lefts.append((pick[0], y))
            rights.append((pick[1], y))
            prev = cx
            miss = 0

        return centers, lefts, rights

    # -------------------------------------------------------------- 后处理
    @staticmethod
    def _smooth(arr: np.ndarray, window: int) -> np.ndarray:
        window = max(1, int(window))
        if window <= 1 or arr.shape[0] < 3:
            return arr
        window = min(window, (arr.shape[0] // 2) * 2 + 1)
        kernel = np.ones(window, np.float32) / window
        pad = window // 2
        padded = np.pad(arr, (pad, pad), mode="edge")
        return np.convolve(padded, kernel, mode="valid").astype(np.float32)

    def _resample(self, pts: np.ndarray, step: int) -> np.ndarray:
        """按 y 均匀重采样并插值断口（pts 为自下而上的 (x, y)）。"""
        if pts.shape[0] < 2:
            return pts
        ys = pts[:, 1][::-1]                  # 升序
        xs = pts[:, 0][::-1]
        y_new = np.arange(ys[0], ys[-1] + 1, max(1, step), dtype=np.float32)
        if y_new.size < 2:
            return pts
        x_new = np.interp(y_new, ys, xs).astype(np.float32)
        return np.stack([x_new, y_new], axis=1)[::-1]   # 还原成自下而上

    # ---------------------------------------------------------------- 主入口
    def detect(self, frame: np.ndarray) -> Detection:
        cfg = self.cfg
        proc, scale = self._to_proc(frame)
        mask = self.build_mask(proc)
        centers, lefts, rights = self._trace(mask)

        det = Detection(mask=mask)
        h, w = mask.shape[:2]
        min_pts = max(4, int(cfg.min_path_ratio * h / max(1, cfg.row_step)))
        if len(centers) < min_pts:
            return det

        pts = np.asarray(centers, dtype=np.float32)
        pts = self._resample(pts, max(1, cfg.row_step))
        pts[:, 0] = self._smooth(pts[:, 0], cfg.smooth_window)

        left_arr = np.asarray(lefts, dtype=np.float32)
        right_arr = np.asarray(rights, dtype=np.float32)
        left_arr[:, 0] = self._smooth(left_arr[:, 0], cfg.smooth_window)
        right_arr[:, 0] = self._smooth(right_arr[:, 0], cfg.smooth_window)

        det.path = pts
        det.left = left_arr
        det.right = right_arr

        y_span = max(1.0, pts[0, 1] - pts[-1, 1])
        det.confidence = float(np.clip(y_span / (0.75 * h), 0.0, 1.0))

        # 前瞻点：距图像底部 lookahead_ratio × 图高
        y_look = h - 1 - cfg.lookahead_ratio * h
        idx = int(np.argmin(np.abs(pts[:, 1] - y_look)))
        lx, ly = int(round(pts[idx, 0])), int(round(pts[idx, 1]))
        det.lookahead = (lx, ly)
        det.offset_px = float(lx - w * 0.5)
        det.offset_ratio = float(np.clip(det.offset_px / (w * 0.5), -1.0, 1.0))

        # 航向：车头（底端点）指向前瞻点
        bx, by = pts[0]
        det.heading_deg = float(np.degrees(np.arctan2(lx - bx, max(1e-6, by - ly))))
        det.scale = scale
        det.valid = True
        return det
