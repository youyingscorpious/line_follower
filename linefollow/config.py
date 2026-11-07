"""巡线算法的可调参数。

所有"像素"参数都以 proc_width 缩放后的处理尺寸为基准，
这样换相机分辨率时不用重新调参。
"""

from __future__ import annotations

from dataclasses import dataclass, asdict


@dataclass
class Config:
    # ---------- 预处理 ----------
    proc_width: int = 960          # 处理分辨率（宽）。原图宽于此值会等比缩小；0 = 不缩放
    blur_ksize: int = 5            # 高斯模糊核，抑制地砖高光和噪点

    # ---------- 黑色胶带分割 ----------
    black_thresh: int = 85         # 绝对阈值：灰度 < 85 视为黑线
    use_adaptive: bool = False     # 是否叠加自适应阈值（光照不均时打开）
    adaptive_block: int = 41       # 自适应阈值的邻域大小（奇数）
    adaptive_c: int = 10           # 自适应阈值的偏置
    open_ksize: int = 3            # 开运算：去掉零散黑点（螺丝、碎屑）
    close_ksize: int = 7           # 闭运算：补齐胶带磨损造成的断口

    # ---------- 扫描线追踪 ----------
    row_step: int = 3              # 每隔几行取一次中心点
    min_line_width: int = 4        # 有效线宽下限（px）
    max_line_width: int = 160      # 有效线宽上限（px），用来排除鞋子等大黑块
    max_jump_ratio: float = 0.12   # 相邻扫描行中心点允许的最大横向跳变（×图宽）
    max_gap_ratio: float = 0.08    # 最多容忍多长的断线（×图高），断线内插值
    start_search_ratio: float = 0.5  # 起点只在这段（图底部往上）里找
    smooth_window: int = 9         # 中心线滑动平均窗口（奇数）

    # ---------- 输出 ----------
    lookahead_ratio: float = 0.30  # 前瞻点位置：距图像底部 30% 图高
    min_path_ratio: float = 0.15   # 有效路径至少要有图高 15% 的长度
    draw_corridor: bool = True     # 是否在窗口里画可行驶走廊

    # ---------- 窗口 ----------
    window_scale: float = 0.72     # 两个窗口相对处理图的显示缩放
    show_fps: bool = False         # 左下角 HUD 里是否显示 fps
    show_hud: bool = False         # 左下角 HUD（状态/offset/heading/fps）与窗口 2 的 NO PATH 文字
    show_border: bool = False      # 窗口 1 四周的状态色边框

    def as_dict(self) -> dict:
        return asdict(self)
