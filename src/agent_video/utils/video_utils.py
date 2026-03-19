"""Utility helpers for video frame manipulation."""

from __future__ import annotations

import base64
import math
from typing import Any, List, Optional, Tuple


def encode_frame_base64(frame: Any, quality: int = 85) -> str:
    """Encode a BGR numpy frame as a base-64 JPEG string.

    Args:
        frame: A numpy ``ndarray`` in BGR format (as returned by OpenCV).
        quality: JPEG compression quality (1–100, higher = better quality).

    Returns:
        Base-64 encoded string of the JPEG data (no ``data:`` prefix).

    Raises:
        ImportError: If ``opencv-python`` is not installed.
        ValueError: If the frame could not be encoded.
    """
    try:
        import cv2  # type: ignore[import]
    except ImportError as exc:
        raise ImportError(
            "opencv-python is required for frame encoding. "
            "Install it with: pip install opencv-python"
        ) from exc

    ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        raise ValueError("Failed to encode frame as JPEG.")
    return base64.b64encode(buf.tobytes()).decode("utf-8")


def resize_frame(frame: Any, width: int, height: int) -> Any:
    """Resize *frame* to ``(width, height)`` using bilinear interpolation.

    Args:
        frame: Source numpy array in BGR format.
        width: Target width in pixels.
        height: Target height in pixels.

    Returns:
        Resized numpy array.

    Raises:
        ImportError: If ``opencv-python`` is not installed.
    """
    try:
        import cv2  # type: ignore[import]
    except ImportError as exc:
        raise ImportError(
            "opencv-python is required for frame resizing. "
            "Install it with: pip install opencv-python"
        ) from exc

    return cv2.resize(frame, (width, height), interpolation=cv2.INTER_LINEAR)


def frames_to_grid(
    frames: List[Any],
    cols: Optional[int] = None,
    cell_size: Tuple[int, int] = (224, 224),
) -> Any:
    """Compose a list of frames into a single grid image.

    This is useful for visualizing many frames at once before sending them
    to a vision-language model as a single image.

    Args:
        frames: List of numpy arrays (BGR, uint8).
        cols: Number of columns in the grid.  Defaults to
            ``ceil(sqrt(len(frames)))``.
        cell_size: ``(width, height)`` of each cell in the grid.

    Returns:
        A single numpy array (BGR, uint8) containing all frames arranged
        in a grid.

    Raises:
        ValueError: If *frames* is empty.
        ImportError: If ``numpy`` or ``opencv-python`` is not installed.
    """
    if not frames:
        raise ValueError("frames list must not be empty.")

    try:
        import cv2  # type: ignore[import]
        import numpy as np  # type: ignore[import]
    except ImportError as exc:
        raise ImportError(
            "opencv-python and numpy are required for frames_to_grid. "
            "Install them with: pip install opencv-python numpy"
        ) from exc

    n = len(frames)
    if cols is None:
        cols = math.ceil(math.sqrt(n))
    rows = math.ceil(n / cols)

    cw, ch = cell_size
    grid = np.zeros((rows * ch, cols * cw, 3), dtype=np.uint8)

    for idx, frame in enumerate(frames):
        r, c = divmod(idx, cols)
        resized = cv2.resize(frame, (cw, ch))
        grid[r * ch : (r + 1) * ch, c * cw : (c + 1) * cw] = resized

    return grid
