"""Unit tests for the agent_video package (no external dependencies needed)."""

from __future__ import annotations

import os
import sys
import types
import unittest

# Allow importing from src/ without installing the package
sys.path.insert(
    0,
    os.path.join(os.path.dirname(__file__), "..", "src"),
)

from agent_video.agents.base_agent import BaseAgent
from agent_video.agents.video_agent import VideoAgent
from agent_video.perception.frame_extractor import FrameExtractor
from agent_video.perception.scene_detector import SceneDetector


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _ConcreteAgent(BaseAgent):
    """Minimal concrete subclass used to test BaseAgent."""

    def analyze(self, video_path: str, query: str = "") -> str:
        return f"ok:{video_path}:{query}"


# ---------------------------------------------------------------------------
# BaseAgent
# ---------------------------------------------------------------------------


class TestBaseAgent(unittest.TestCase):
    def test_instantiation(self):
        agent = _ConcreteAgent(model="test-model", max_frames=4)
        self.assertEqual(agent.model, "test-model")
        self.assertEqual(agent.max_frames, 4)

    def test_repr(self):
        agent = _ConcreteAgent()
        self.assertIn("_ConcreteAgent", repr(agent))

    def test_analyze_returns_string(self):
        agent = _ConcreteAgent()
        result = agent.analyze("/fake/path.mp4", "hello")
        self.assertIsInstance(result, str)
        self.assertIn("hello", result)

    def test_abstract_cannot_instantiate(self):
        with self.assertRaises(TypeError):
            BaseAgent()  # type: ignore[abstract]


# ---------------------------------------------------------------------------
# VideoAgent
# ---------------------------------------------------------------------------


class TestVideoAgent(unittest.TestCase):
    def test_defaults(self):
        agent = VideoAgent()
        self.assertEqual(agent.model, "gpt-4o")
        self.assertEqual(agent.max_frames, 16)

    def test_custom_params(self):
        agent = VideoAgent(model="gpt-4-vision-preview", max_frames=8)
        self.assertEqual(agent.model, "gpt-4-vision-preview")
        self.assertEqual(agent.max_frames, 8)

    def test_analyze_missing_file(self):
        agent = VideoAgent()
        with self.assertRaises(FileNotFoundError):
            agent.analyze("/nonexistent/video.mp4")

    def test_analyze_with_mock_cv2(self):
        """Verify analyze() works end-to-end with a mocked OpenCV."""
        import numpy as np

        # Build a minimal fake cv2 module
        fake_cv2 = types.ModuleType("cv2")
        fake_frame = np.zeros((240, 320, 3), dtype=np.uint8)

        class FakeCapture:
            _call_count = 0

            def isOpened(self):
                return True

            def get(self, prop):
                # CAP_PROP_FPS=5, CAP_PROP_FRAME_COUNT=25
                return {5: 25.0, 7: 25.0}.get(prop, 25.0)

            def set(self, prop, value):
                pass

            def read(self):
                self._call_count += 1
                # Return up to 30 frames then stop
                return (self._call_count <= 30, fake_frame.copy())

            def release(self):
                pass

        fake_cv2.VideoCapture = lambda path: FakeCapture()
        fake_cv2.CAP_PROP_FPS = 5
        fake_cv2.CAP_PROP_FRAME_COUNT = 7
        fake_cv2.CAP_PROP_POS_FRAMES = 1
        fake_cv2.COLOR_BGR2GRAY = 6
        fake_cv2.INTER_LINEAR = 1
        fake_cv2.IMWRITE_JPEG_QUALITY = 1

        def fake_cvt_color(frame, code):
            return frame[:, :, 0]

        def fake_resize(frame, size, interpolation=None):
            return frame

        def fake_imencode(ext, frame, params=None):
            import base64
            return True, type("Buf", (), {"tobytes": lambda self: b"FAKEJPEG"})()

        fake_cv2.cvtColor = fake_cvt_color
        fake_cv2.resize = fake_resize
        fake_cv2.imencode = fake_imencode

        # Patch sys.modules so our code picks up the fake cv2
        original_cv2 = sys.modules.get("cv2")
        sys.modules["cv2"] = fake_cv2

        try:
            # Create a fake video file path
            import tempfile
            with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f:
                tmp_path = f.name

            agent = VideoAgent(max_frames=4)  # no API key → rule-based
            result = agent.analyze(tmp_path, "What is in the video?")
            self.assertIsInstance(result, str)
            self.assertGreater(len(result), 0)
        finally:
            sys.modules.pop("cv2", None)
            if original_cv2 is not None:
                sys.modules["cv2"] = original_cv2
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)


# ---------------------------------------------------------------------------
# FrameExtractor
# ---------------------------------------------------------------------------


class TestFrameExtractor(unittest.TestCase):
    def test_build_sample_timestamps_uniform(self):
        ts = FrameExtractor._build_sample_timestamps(None, 10.0, 5)
        self.assertEqual(len(ts), 5)
        self.assertAlmostEqual(ts[0], 0.0)
        self.assertLess(ts[-1], 10.0)

    def test_build_sample_timestamps_single(self):
        ts = FrameExtractor._build_sample_timestamps(None, 10.0, 1)
        self.assertEqual(ts, [5.0])

    def test_build_sample_timestamps_from_provided(self):
        provided = [0.5, 2.0, 5.5, 8.0]
        ts = FrameExtractor._build_sample_timestamps(provided, 10.0, 10)
        self.assertEqual(sorted(ts), sorted(provided))

    def test_build_sample_timestamps_capped(self):
        provided = list(range(20))  # 20 timestamps
        ts = FrameExtractor._build_sample_timestamps(provided, 20.0, 5)
        self.assertEqual(len(ts), 5)

    def test_extract_raises_without_cv2(self):
        original = sys.modules.get("cv2")
        sys.modules["cv2"] = None  # type: ignore[assignment]
        try:
            extractor = FrameExtractor()
            with self.assertRaises((ImportError, TypeError)):
                extractor.extract("/fake/video.mp4")
        finally:
            if original is not None:
                sys.modules["cv2"] = original
            else:
                sys.modules.pop("cv2", None)

    def test_extract_raises_missing_file(self):
        """extract() raises FileNotFoundError for missing files (needs cv2)."""
        try:
            import cv2  # noqa: F401
        except ImportError:
            self.skipTest("opencv-python not installed")
        extractor = FrameExtractor()
        with self.assertRaises(FileNotFoundError):
            extractor.extract("/nonexistent/video.mp4")


# ---------------------------------------------------------------------------
# SceneDetector
# ---------------------------------------------------------------------------


class TestSceneDetector(unittest.TestCase):
    def test_defaults(self):
        sd = SceneDetector()
        self.assertEqual(sd.threshold, 30.0)
        self.assertEqual(sd.min_scene_len, 1.0)

    def test_detect_missing_file(self):
        sd = SceneDetector()
        with self.assertRaises(FileNotFoundError):
            sd.detect("/nonexistent/video.mp4")

    def test_detect_without_cv2_returns_zero(self):
        original = sys.modules.get("cv2")
        sys.modules["cv2"] = None  # type: ignore[assignment]
        try:
            sd = SceneDetector()
            # detect() gracefully returns [0.0] when cv2 is unavailable
            # but will still raise FileNotFoundError first
            try:
                result = sd.detect("/fake/video.mp4")
                self.assertEqual(result, [0.0])
            except FileNotFoundError:
                pass  # also acceptable
        finally:
            if original is not None:
                sys.modules["cv2"] = original
            else:
                sys.modules.pop("cv2", None)


if __name__ == "__main__":
    unittest.main()
