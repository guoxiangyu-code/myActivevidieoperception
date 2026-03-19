"""Frame extraction from video files."""

from __future__ import annotations

import math
from typing import Any, List, Optional, Sequence


class FrameExtractor:
    """Extract key frames from a video file.

    Uses OpenCV when available; falls back to a stub implementation that
    raises :class:`ImportError` when ``cv2`` is absent.

    Args:
        target_size: Optional ``(width, height)`` tuple to resize every
            extracted frame to.  When *None* frames keep their original size.
    """

    def __init__(self, target_size: Optional[tuple[int, int]] = None) -> None:
        self.target_size = target_size

    def extract(
        self,
        video_path: str,
        timestamps: Optional[Sequence[float]] = None,
        max_frames: int = 16,
    ) -> List[Any]:
        """Extract frames from *video_path*.

        When *timestamps* are provided one frame is sampled near each
        timestamp.  Otherwise frames are sampled uniformly across the
        video duration.  The total number of returned frames is capped at
        *max_frames*.

        Args:
            video_path: Path to the video file.
            timestamps: Optional list of timestamps (seconds) at which to
                sample frames.  Typically the output of
                :class:`~agent_video.perception.scene_detector.SceneDetector`.
            max_frames: Hard limit on the number of frames returned.

        Returns:
            List of numpy ``ndarray`` objects (BGR, uint8), one per frame.

        Raises:
            ImportError: If ``opencv-python`` is not installed.
            FileNotFoundError: If *video_path* does not exist.
        """
        try:
            import cv2  # type: ignore[import]
        except ImportError as exc:
            raise ImportError(
                "opencv-python is required for frame extraction. "
                "Install it with: pip install opencv-python"
            ) from exc

        import os

        if not os.path.exists(video_path):
            raise FileNotFoundError(f"Video file not found: {video_path}")

        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            raise ValueError(f"Could not open video: {video_path}")

        try:
            fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
            total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            duration = total_frames / fps

            sample_ts = self._build_sample_timestamps(
                timestamps, duration, max_frames
            )

            frames: List[Any] = []
            for ts in sample_ts:
                frame = self._read_frame_at(cap, ts, fps)
                if frame is not None:
                    if self.target_size is not None:
                        frame = cv2.resize(frame, self.target_size)
                    frames.append(frame)
        finally:
            cap.release()

        return frames

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_sample_timestamps(
        timestamps: Optional[Sequence[float]],
        duration: float,
        max_frames: int,
    ) -> List[float]:
        """Compute the timestamps at which to sample frames.

        Args:
            timestamps: Scene-boundary timestamps or *None* for uniform
                sampling.
            duration: Total video duration in seconds.
            max_frames: Maximum number of timestamps to return.

        Returns:
            Sorted list of timestamps (seconds).
        """
        if timestamps:
            ts = sorted(set(timestamps))
            if len(ts) > max_frames:
                step = len(ts) / max_frames
                ts = [ts[int(i * step)] for i in range(max_frames)]
            return ts

        if duration <= 0 or max_frames <= 0:
            return []

        if max_frames == 1:
            return [duration / 2.0]

        step = duration / (max_frames - 1)
        return [min(i * step, duration - 0.001) for i in range(max_frames)]

    @staticmethod
    def _read_frame_at(cap: Any, timestamp: float, fps: float) -> Optional[Any]:
        """Seek *cap* to *timestamp* seconds and return the decoded frame.

        Args:
            cap: An open ``cv2.VideoCapture`` object.
            timestamp: Target timestamp in seconds.
            fps: Video frame rate.

        Returns:
            The decoded frame (numpy array) or *None* on failure.
        """
        try:
            import cv2  # type: ignore[import]
        except ImportError:
            return None

        frame_idx = int(timestamp * fps)
        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
        ok, frame = cap.read()
        return frame if ok else None
