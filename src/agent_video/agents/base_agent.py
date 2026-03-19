"""Base agent class for video understanding agents."""

from __future__ import annotations

import abc
from typing import Any


class BaseAgent(abc.ABC):
    """Abstract base class for all video-understanding agents.

    Subclasses must implement :meth:`analyze` which receives a video path
    and an optional natural-language query and returns a plain-text answer.
    """

    def __init__(self, model: str = "gpt-4o", max_frames: int = 16) -> None:
        """Initialise the agent.

        Args:
            model: Name of the vision-language model to use for reasoning.
            max_frames: Maximum number of video frames to pass to the model
                per analysis call.
        """
        self.model = model
        self.max_frames = max_frames

    @abc.abstractmethod
    def analyze(self, video_path: str, query: str = "") -> str:
        """Analyze a video and return a natural-language answer.

        Args:
            video_path: Path to the local video file.
            query: Optional question or instruction for the agent.

        Returns:
            A plain-text answer produced by the agent.
        """

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(model={self.model!r}, max_frames={self.max_frames})"
