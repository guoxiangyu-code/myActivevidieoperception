"""Video agent that uses a vision-language model to understand video content."""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from .base_agent import BaseAgent
from ..perception.frame_extractor import FrameExtractor
from ..perception.scene_detector import SceneDetector
from ..utils.video_utils import encode_frame_base64


_DEFAULT_SYSTEM_PROMPT = (
    "You are an expert video analyst. "
    "You will be shown a sequence of key frames sampled from a video. "
    "Analyze the frames carefully and answer the user's question accurately and concisely."
)


class VideoAgent(BaseAgent):
    """Agent that actively perceives video content and answers questions.

    The agent works in three stages:

    1. **Scene detection** – identify scene boundaries to sample diverse frames.
    2. **Frame extraction** – pull the most informative frames from each scene.
    3. **LLM reasoning** – send the frames plus the user query to a
       vision-language model and return its answer.

    When no API key / client is available the agent falls back to a
    lightweight rule-based description mode so that the class is still
    usable without external dependencies.

    Args:
        model: Vision-language model identifier (e.g. ``"gpt-4o"``).
        max_frames: Maximum number of frames forwarded to the model.
        system_prompt: System-level instruction sent to the LLM.
        openai_api_key: OpenAI API key.  Falls back to the
            ``OPENAI_API_KEY`` environment variable when *None*.
    """

    def __init__(
        self,
        model: str = "gpt-4o",
        max_frames: int = 16,
        system_prompt: str = _DEFAULT_SYSTEM_PROMPT,
        openai_api_key: Optional[str] = None,
    ) -> None:
        super().__init__(model=model, max_frames=max_frames)
        self.system_prompt = system_prompt
        self._api_key = openai_api_key or os.environ.get("OPENAI_API_KEY", "")

        self._frame_extractor = FrameExtractor()
        self._scene_detector = SceneDetector()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def analyze(self, video_path: str, query: str = "") -> str:
        """Analyze *video_path* and answer *query*.

        Args:
            video_path: Path to the video file to analyse.
            query: Natural-language question or instruction.

        Returns:
            A natural-language answer string.

        Raises:
            FileNotFoundError: If *video_path* does not exist.
        """
        if not os.path.exists(video_path):
            raise FileNotFoundError(f"Video file not found: {video_path}")

        query = query.strip() or "Describe what is happening in this video."

        # Step 1 – detect scenes
        scene_timestamps = self._scene_detector.detect(video_path)

        # Step 2 – extract key frames
        frames = self._frame_extractor.extract(
            video_path,
            timestamps=scene_timestamps,
            max_frames=self.max_frames,
        )

        if not frames:
            return "No frames could be extracted from the provided video."

        # Step 3 – call LLM (or fall back to rule-based description)
        if self._api_key:
            return self._call_llm(frames, query)
        return self._rule_based_description(frames, query, video_path)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _call_llm(self, frames: List[Any], query: str) -> str:
        """Send *frames* and *query* to the vision-language model.

        Requires the ``openai`` package to be installed.

        Args:
            frames: List of numpy arrays (BGR, uint8) representing key frames.
            query: User question / instruction.

        Returns:
            The model's text response.
        """
        try:
            import openai  # type: ignore[import]
        except ImportError as exc:
            raise ImportError(
                "The 'openai' package is required for LLM-based analysis. "
                "Install it with: pip install openai"
            ) from exc

        client = openai.OpenAI(api_key=self._api_key)

        # Build the multimodal message content
        content: List[Dict[str, Any]] = [{"type": "text", "text": query}]
        for frame in frames:
            b64 = encode_frame_base64(frame)
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{b64}", "detail": "low"},
                }
            )

        response = client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": content},
            ],
            max_tokens=1024,
        )

        return response.choices[0].message.content or ""

    def _rule_based_description(
        self, frames: List[Any], query: str, video_path: str
    ) -> str:
        """Provide a basic description when no LLM is available.

        Args:
            frames: Extracted key frames.
            query: Original user query.
            video_path: Path used for context.

        Returns:
            A simple descriptive string.
        """
        import os

        filename = os.path.basename(video_path)
        n = len(frames)
        h, w = frames[0].shape[:2] if frames else (0, 0)
        return (
            f"[Rule-based] Analyzed '{filename}': extracted {n} key frame(s) "
            f"at {w}×{h} resolution. "
            f"Query received: '{query}'. "
            "Set OPENAI_API_KEY to enable LLM-based analysis."
        )
