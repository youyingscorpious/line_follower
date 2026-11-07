"""OpenCV 黑胶带巡线检测。"""

from .config import Config
from .detector import Detection, LineDetector

__all__ = ["Config", "LineDetector", "Detection"]
