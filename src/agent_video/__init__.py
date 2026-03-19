"""
agent_video – Agent-based Active Video Understanding library.
"""

from .agents.video_agent import VideoAgent
from .perception.frame_extractor import FrameExtractor
from .perception.scene_detector import SceneDetector

__all__ = ["VideoAgent", "FrameExtractor", "SceneDetector"]
__version__ = "0.1.0"
