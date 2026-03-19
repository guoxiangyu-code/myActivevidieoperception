"""Scene boundary detection for video files."""

from __future__ import annotations

from typing import Any, List, Optional


class SceneDetector:
    """Detect scene boundaries in a video file.

    Scene boundaries are identified by measuring the pixel-level difference
    between consecutive frames.  When the difference exceeds *threshold* a
    new scene is assumed to have started.

    Args:
        threshold: Mean absolute pixel difference (0–255) above which a
            frame transition is classified as a scene cut.
        min_scene_len: Minimum length of a scene in seconds.  Cuts that
            are closer together than this value are ignored.
    """

    def __init__(
        self,
        threshold: float = 30.0,
        min_scene_len: float = 1.0,
    ) -> None:
        self.threshold = threshold
        self.min_scene_len = min_scene_len

    def detect(self, video_path: str) -> List[float]:
        """Return a list of scene-start timestamps (in seconds).

        The first timestamp is always ``0.0``.  Subsequent timestamps mark
        the start of a new scene as identified by pixel-difference analysis.

        Args:
            video_path: Path to the video file.

        Returns:
            Sorted list of scene-start timestamps in seconds.  Returns
            ``[0.0]`` when the video contains a single scene or when
            OpenCV is unavailable.

        Raises:
            FileNotFoundError: If *video_path* does not exist.
        """
        import os

        if not os.path.exists(video_path):
            raise FileNotFoundError(f"Video file not found: {video_path}")

        try:
            import cv2  # type: ignore[import]
            import numpy as np  # type: ignore[import]
        except ImportError:
            # Gracefully fall back – return just the video start
            return [0.0]

        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            return [0.0]

        try:
            fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
            timestamps: List[float] = [0.0]
            prev_gray: Optional[Any] = None
            last_cut_time = 0.0
            frame_idx = 0

            while True:
                ok, frame = cap.read()
                if not ok:
                    break

                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                current_time = frame_idx / fps

                if prev_gray is not None:
                    diff = float(np.mean(np.abs(gray.astype(np.int32) - prev_gray.astype(np.int32))))
                    if (
                        diff > self.threshold
                        and (current_time - last_cut_time) >= self.min_scene_len
                    ):
                        timestamps.append(current_time)
                        last_cut_time = current_time

                prev_gray = gray
                frame_idx += 1
        finally:
            cap.release()

        return sorted(set(timestamps))
