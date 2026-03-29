"""QwenClient – OpenAI-compatible adapter that mirrors GeminiClient's interface.

Uses the OpenAI Python SDK to call Qwen3-Omni (DashScope) or any
OpenAI-compatible endpoint while exposing the same public surface as
``GeminiClient`` so that ``Controller``, ``Observer``, ``Reflector``, and
``eval_dataset`` can swap backends transparently.

Supports native video input via base64 data URLs (with audio preserved),
leveraging Qwen3-Omni's native video+audio understanding capability.
"""
from __future__ import annotations

import base64
import dataclasses
from dataclasses import dataclass, field
import json
import os
import re
import time
from math import floor, ceil
from typing import Any, Dict, List, Optional, Tuple, TYPE_CHECKING

from .prompt import (
    PromptManager,
    parse_json_response,
    validate_against_schema,
    PLAN_SCHEMA,
    EVIDENCE_SCHEMA,
    FINAL_ANSWER_SCHEMA,
    MCQ_SCHEMA,
)
from .video_utils import (
    VideoMetadataExtractor,
    get_mime_type,
    resolve_video_path,
    create_video_clip,
    create_reencoded_video_clip,
    round_intervals_full_seconds,
)

if TYPE_CHECKING:
    from .main import PlanSpec, Evidence, Blackboard, WatchConfig


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# ------------------------------------------------------------------
# OpenAI-compat proxy for Reflector direct-access pattern
# ------------------------------------------------------------------

class _CompatResponse:
    """Mimics a Gemini response object with a ``.text`` attribute."""

    def __init__(self, text: str):
        self.text = text


class _OpenAICompatProxy:
    """Proxy that exposes ``.models.generate_content()`` so the Reflector
    class in ``main.py`` (which calls
    ``self.client.client.models.generate_content(model=…, contents=…)``)
    works unchanged.
    """

    def __init__(self, openai_client: Any, default_model: str):
        self.models = self  # .models resolves to self
        self._client = openai_client
        self._model = default_model

    def generate_content(
        self,
        model: Optional[str] = None,
        contents: Any = None,
        config: Any = None,
    ) -> _CompatResponse:
        model = model or self._model
        if isinstance(contents, str):
            text_content = contents
        elif isinstance(contents, list):
            text_content = "\n".join(str(c) for c in contents)
        else:
            text_content = str(contents)
        resp = self._client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": text_content}],
            modalities=["text"],
        )
        return _CompatResponse(resp.choices[0].message.content)


# ------------------------------------------------------------------
# QwenClient
# ------------------------------------------------------------------

class QwenClient:
    """Drop-in replacement for ``GeminiClient`` using an OpenAI-compatible
    Qwen endpoint.

    All public attributes and method signatures match ``GeminiClient`` so that
    ``Controller``, ``Observer``, ``Reflector``, and ``eval_dataset`` need no
    changes to use this backend.
    """

    def __init__(
        self,
        model: str = "qwen3-omni-flash",
        plan_replan_model: Optional[str] = None,
        execute_model: Optional[str] = None,
        project: str = "",
        location: str = "",
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        debug: bool = False,
        max_frame_low: int = 240,
        max_frame_medium: int = 128,
        max_frame_high: int = 128,
        prefer_compressed: bool = True,
        keep_temp_clips: bool = False,
        qwen_video_mode: str = "video",
    ):
        self.plan_replan_model = plan_replan_model if plan_replan_model is not None else model
        self.execute_model = execute_model if execute_model is not None else model
        self.model = model
        self.project = project
        self.location = location
        self.api_key = api_key or ""
        self.base_url = (base_url or os.getenv("QWEN_BASE_URL", "")).strip()
        self.client: Optional[_OpenAICompatProxy] = None
        self.debug = debug
        self.max_frame_low = max_frame_low
        self.max_frame_medium = max_frame_medium
        self.max_frame_high = max_frame_high
        self.prefer_compressed = prefer_compressed
        self.keep_temp_clips = keep_temp_clips
        self.created_clips: List[str] = []
        self.temp_clips_dir: Optional[str] = None
        self.qwen_video_mode = qwen_video_mode
        self._openai_client: Any = None

    # ------------------------------------------------------------------
    # Client lifecycle
    # ------------------------------------------------------------------

    @staticmethod
    def _ensure_v1(url: str) -> str:
        """Ensure *url* ends with ``/v1`` (or another version segment)."""
        if not url:
            return "https://dashscope.aliyuncs.com/compatible-mode/v1"
        stripped = url.rstrip("/")
        if re.search(r'/v\d+(?:beta)?$', stripped):
            return stripped
        return stripped + "/v1"

    def initialize_client(self) -> None:
        """Create the OpenAI SDK client and the Reflector-compat proxy."""
        try:
            import openai

            effective_url = self._ensure_v1(self.base_url)
            self._openai_client = openai.OpenAI(
                api_key=self.api_key,
                base_url=effective_url,
            )
            self.client = _OpenAICompatProxy(self._openai_client, self.plan_replan_model)
            if self.debug:
                print(f"✅ Initialized Qwen client (base_url={effective_url})")
        except Exception as e:
            print(f"❌ Failed to initialize Qwen client: {e}")
            raise

    # ------------------------------------------------------------------
    # Low-level API helpers
    # ------------------------------------------------------------------

    def _call_text_api(self, model: str, prompt: str) -> str:
        """Text-only call (planning / synthesis)."""
        if self._openai_client is None:
            self.initialize_client()
        resp = self._openai_client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            modalities=["text"],
        )
        return resp.choices[0].message.content

    def _call_video_api(self, model: str, prompt: str, video_content_blocks: List[Dict[str, Any]]) -> str:
        """Multi-modal call with video / image content blocks.

        Automatically dispatches to dashscope SDK for local file paths
        (large videos) or openai SDK for base64 content (small clips).
        """
        if self._openai_client is None:
            self.initialize_client()

        # Check for local file path markers (large videos that exceed base64 limit)
        local_files = [b["path"] for b in video_content_blocks if b.get("type") == "_local_video"]
        if local_files:
            return self._call_video_api_dashscope(model, prompt, local_files)

        # Standard openai SDK path for base64 inline content
        content = video_content_blocks + [{"type": "text", "text": prompt}]
        resp = self._openai_client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": content}],
            modalities=["text"],
        )
        return resp.choices[0].message.content

    def _call_video_api_dashscope(self, model: str, prompt: str, video_paths: List[str]) -> str:
        """Call DashScope API with local video files (auto-uploaded to OSS)."""
        import dashscope
        from dashscope import MultiModalConversation

        dashscope.api_key = self.api_key
        content = [{'video': p} for p in video_paths]
        content.append({'text': prompt})
        messages = [{'role': 'user', 'content': content}]

        response = MultiModalConversation.call(
            model=model,
            messages=messages,
            modalities=['text'],
        )
        if response.status_code != 200:
            raise RuntimeError(
                f"DashScope API error {response.status_code}: "
                f"{getattr(response, 'message', response)}"
            )
        return response.output.choices[0].message.content[0]['text']

    # ------------------------------------------------------------------
    # Video helpers
    # ------------------------------------------------------------------

    def _extract_frames(
        self,
        video_path: str,
        fps: float,
        start_sec: Optional[float] = None,
        end_sec: Optional[float] = None,
        max_frames: int = 128,
        resize_short_edge: Optional[int] = None,
    ) -> List[str]:
        """Extract frames as base64-encoded JPEGs using OpenCV."""
        import cv2

        cap = cv2.VideoCapture(video_path)
        video_fps = cap.get(cv2.CAP_PROP_FPS)
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        duration = total_frames / video_fps if video_fps > 0 else 0

        start = start_sec or 0.0
        end = end_sec or duration

        interval = 1.0 / fps if fps > 0 else 1.0
        timestamps: List[float] = []
        t = start
        while t < end and len(timestamps) < max_frames:
            timestamps.append(t)
            t += interval

        frames_b64: List[str] = []
        for ts in timestamps:
            frame_idx = int(ts * video_fps)
            cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
            ret, frame = cap.read()
            if not ret:
                continue
            if resize_short_edge:
                h, w = frame.shape[:2]
                if min(h, w) > resize_short_edge:
                    scale = resize_short_edge / min(h, w)
                    frame = cv2.resize(frame, (int(w * scale), int(h * scale)))
            _, buf = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
            frames_b64.append(base64.b64encode(buf).decode())
        cap.release()
        return frames_b64

    def create_video_part(
        self,
        video_path: str,
        fps: Optional[float] = None,
        start_offset: Optional[str] = None,
        end_offset: Optional[str] = None,
        media_resolution: Optional[str] = None,
        duration_sec: Optional[float] = None,
    ) -> List[Dict[str, Any]]:
        """Create OpenAI content blocks for a video.

        Returns a **list** of dicts (``[{"type": "video_url", …}]`` or
        ``[{"type": "image_url", …}, …]``) instead of a Gemini ``Part``.
        """
        actual_video_path, compressed_path = resolve_video_path(
            video_path,
            prefer_compressed=self.prefer_compressed,
        )

        if not os.path.exists(actual_video_path):
            raise FileNotFoundError(f"Video file not found: {actual_video_path}")

        if self.debug and compressed_path:
            print(f"📹 Using compressed video: {actual_video_path}")

        # --- FPS clamping (mirrors GeminiClient lines 766-814) ---
        if fps:
            if media_resolution == "low":
                max_frame = self.max_frame_low
            elif media_resolution == "medium":
                max_frame = self.max_frame_medium
            elif media_resolution == "high":
                max_frame = self.max_frame_high
            else:
                max_frame = self.max_frame_medium

            duration = duration_sec
            if duration is None or duration <= 0:
                if start_offset and end_offset:
                    try:
                        s_sec = float(start_offset.rstrip("s"))
                        e_sec = float(end_offset.rstrip("s"))
                        duration = max(0, e_sec - s_sec)
                    except ValueError:
                        duration = None
                elif end_offset:
                    try:
                        duration = float(end_offset.rstrip("s"))
                    except ValueError:
                        duration = None
            if duration is None or duration <= 0:
                meta_ext = VideoMetadataExtractor(actual_video_path)
                vid_dur = meta_ext.duration
                if start_offset:
                    try:
                        s_sec = float(start_offset.rstrip("s"))
                        duration = max(0, vid_dur - s_sec)
                    except ValueError:
                        duration = vid_dur
                else:
                    duration = vid_dur

            if duration and duration > 0:
                expected_frames = fps * duration
                if expected_frames > max_frame:
                    adjusted_fps = max_frame / duration
                    if self.debug:
                        print(f"⚠️  Adjusting FPS: {fps:.2f} -> {adjusted_fps:.2f} (max {max_frame} frames)")
                    fps = adjusted_fps

        # --- Parse offset seconds for frame extraction ---
        start_sec: Optional[float] = None
        end_sec: Optional[float] = None
        if start_offset:
            try:
                start_sec = float(start_offset.rstrip("s"))
            except ValueError:
                pass
        if end_offset:
            try:
                end_sec = float(end_offset.rstrip("s"))
            except ValueError:
                pass

        if self.debug:
            print(f"📹 Video metadata: fps={fps}, startOffset={start_offset}, endOffset={end_offset}, mode={self.qwen_video_mode}")

        # --- Determine effective video duration for hybrid mode decision ---
        effective_duration = duration_sec or 0
        if (effective_duration <= 0) and start_sec is not None and end_sec is not None:
            effective_duration = max(0, end_sec - start_sec)

        # --- Build content blocks based on qwen_video_mode ---
        # Hybrid logic: for long videos (>10min), use frames for overview scans,
        # native video for shorter targeted clips (where audio matters).
        _LONG_VIDEO_THRESHOLD = 600  # 10 minutes
        use_frames = False
        if self.qwen_video_mode == "frames":
            use_frames = True
        elif self.qwen_video_mode == "video" and effective_duration > _LONG_VIDEO_THRESHOLD:
            if self.debug:
                print(f"⚠️  Video segment {effective_duration:.0f}s > {_LONG_VIDEO_THRESHOLD}s, using frames for overview scan")
            use_frames = True

        if use_frames:
            return self._build_frames_blocks(actual_video_path, fps, start_sec, end_sec, media_resolution)
        elif self.qwen_video_mode == "auto":
            try:
                return self._build_video_block(actual_video_path)
            except Exception:
                if self.debug:
                    print("⚠️  video mode failed, falling back to frames")
                return self._build_frames_blocks(actual_video_path, fps, start_sec, end_sec, media_resolution)
        else:
            # default: "video" for short clips
            return self._build_video_block(actual_video_path)

    # Maximum file size for base64 inline encoding (~14MB → ~19MB base64, under DashScope 20MB limit)
    _MAX_INLINE_BYTES = 14 * 1024 * 1024

    def _build_video_block(self, video_path: str) -> List[Dict[str, Any]]:
        """Build video content block. Uses base64 inline for small files,
        local file path marker for large files (dispatched to dashscope SDK)."""
        file_size = os.path.getsize(video_path)
        if file_size > self._MAX_INLINE_BYTES:
            if self.debug:
                print(f"📦 Video {file_size/1024/1024:.1f}MB > 14MB limit, using dashscope file upload")
            return [{"type": "_local_video", "path": os.path.abspath(video_path)}]
        mime = get_mime_type(video_path)
        with open(video_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        return [{"type": "video_url", "video_url": {"url": f"data:{mime};base64,{b64}"}}]

    def _build_frames_blocks(
        self,
        video_path: str,
        fps: Optional[float],
        start_sec: Optional[float],
        end_sec: Optional[float],
        media_resolution: Optional[str],
    ) -> List[Dict[str, Any]]:
        """Extract frames and return as ``image_url`` blocks."""
        effective_fps = fps or 1.0
        if media_resolution == "low":
            max_frames = self.max_frame_low
        elif media_resolution == "high":
            max_frames = self.max_frame_high
        else:
            max_frames = self.max_frame_medium

        frames_b64 = self._extract_frames(
            video_path,
            effective_fps,
            start_sec=start_sec,
            end_sec=end_sec,
            max_frames=max_frames,
        )
        return [
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}}
            for b64 in frames_b64
        ]

    # ------------------------------------------------------------------
    # Rate → resolution mapping
    # ------------------------------------------------------------------

    def _map_rate_to_media_res(self, rate: Any) -> str:
        """Map ``SpatialTokenRate`` or plain string to ``low|medium|high``."""
        if hasattr(rate, "value"):
            return rate.value
        return str(rate).strip().lower()

    # ------------------------------------------------------------------
    # plan()
    # ------------------------------------------------------------------

    def plan(
        self,
        query: str,
        video_meta: Optional[Dict[str, Any]] = None,
        prior: Optional["Blackboard"] = None,
        options: Optional[List[str]] = None,
        store: Any = None,
        round_id: int = 0,
    ) -> "PlanSpec":
        """Generate an observation action plan (mirrors ``GeminiClient.plan``)."""
        from .main import (
            SpatialTokenRate,
            WatchConfig,
            PlanSpec,
            _apply_temporal_plan_guards,
        )

        if self.client is None:
            self.initialize_client()

        if video_meta is None:
            video_meta = {}

        is_replan = prior is not None and len(prior.evidences) > 0

        if is_replan:
            evidence_summary = prior.summary_text()
            prompt = PromptManager.get_replanning_prompt(query, video_meta, evidence_summary, options)
        else:
            prompt = PromptManager.get_planning_prompt(query, video_meta, options)

        if self.debug:
            print(f"\n{'=' * 80}")
            print(f"🎯 PLANNER INPUT {'(REPLAN)' if is_replan else '(INITIAL)'}")
            print(f"{'=' * 80}")
            print(f"Query: {query}")
            print(f"Video Duration: {video_meta.get('duration_sec', 'unknown')}s")
            if options:
                print(f"Options: {options}")
            if is_replan:
                print(f"Prior Evidence Rounds: {len(prior.evidences)}")
                print(f"Evidence Summary: {evidence_summary[:200]}...")
            print(f"{'=' * 80}\n")

        response_text = self._call_text_api(self.plan_replan_model, prompt)

        if store is not None:
            role = "planner_replan" if is_replan else "planner"
            store.append_role_trace(role, round_id, prompt, response_text)

        if self.debug:
            print(f"\n{'=' * 80}")
            print(f"📋 PLANNER OUTPUT (Raw)")
            print(f"{'=' * 80}")
            print(response_text[:500] + "..." if len(response_text) > 500 else response_text)
            print(f"{'=' * 80}\n")

        plan_data = parse_json_response(response_text)

        if plan_data is None or not validate_against_schema(plan_data, PLAN_SCHEMA):
            if self.debug:
                print("⚠️  Failed to parse LLM plan, using fallback")
            return self._get_fallback_plan(query)

        try:
            steps_list = plan_data.get("steps", [])
            if not steps_list:
                if self.debug:
                    print("⚠️  No steps in plan, using fallback")
                return self._get_fallback_plan(query)

            s = steps_list[0]

            spatial_rate_str = str(s["spatial_token_rate"]).strip().lower()
            try:
                spatial_token_rate = SpatialTokenRate(spatial_rate_str)
            except ValueError:
                if self.debug:
                    print(f"⚠️  Invalid spatial_token_rate: '{s['spatial_token_rate']}', using 'low' as default")
                spatial_token_rate = SpatialTokenRate.low

            regions: List[Tuple[float, float]] = []
            raw_regions = s.get("regions", [])
            if raw_regions:
                for r in raw_regions:
                    try:
                        if not isinstance(r, (list, tuple)) or len(r) != 2:
                            if self.debug:
                                print(f"⚠️  Invalid region format (expected [start, end]): {r}, skipping")
                            continue
                        start_val = r[0]
                        end_val = r[1]
                        if isinstance(start_val, str) and isinstance(end_val, str):
                            if self.debug:
                                print(f"⚠️  Region contains non-numeric values: {r}, skipping")
                            continue
                        regions.append((float(start_val), float(end_val)))
                    except (ValueError, TypeError) as exc:
                        if self.debug:
                            print(f"⚠️  Error parsing region {r}: {exc}, skipping")
                        continue

            watch = WatchConfig(
                load_mode=s["load_mode"],
                fps=float(s["fps"]),
                spatial_token_rate=spatial_token_rate,
                regions=regions,
            )

            plan = PlanSpec(
                plan_version="v1",
                query=query,
                watch=watch,
                description=s.get("description", ""),
                completion_criteria=plan_data.get("completion_criteria", ""),
            )
            plan = _apply_temporal_plan_guards(plan, query, video_meta, debug=self.debug)

            if self.debug:
                print(f"\n{'=' * 80}")
                print(f"✅ PLANNER OUTPUT (Parsed)")
                print(f"{'=' * 80}")
                print("Generated single observation action")
                try:
                    print(f"Load Mode: {plan.watch.load_mode}")
                    print(f"FPS: {plan.watch.fps}")
                    print(f"Spatial Resolution: {plan.watch.spatial_token_rate.value if hasattr(plan.watch.spatial_token_rate, 'value') else plan.watch.spatial_token_rate}")
                    print(f"Regions: {plan.watch.regions}")
                    print(f"Description: {plan.description}")
                    print(f"\nFull Plan JSON:")
                    print(json.dumps(dataclasses.asdict(plan), indent=2, ensure_ascii=False))
                except Exception as exc:
                    print(f"Error printing plan: {exc}")
                print(f"{'=' * 80}\n")

            return plan

        except Exception as exc:
            if self.debug:
                print(f"⚠️  Error converting plan: {exc}, using fallback")
            return self._get_fallback_plan(query)

    # ------------------------------------------------------------------
    # infer_on_video()
    # ------------------------------------------------------------------

    def infer_on_video(
        self,
        video_path: str,
        duration_sec: float,
        sub_query: str,
        context: str,
        start_sec: float,
        end_sec: float,
        watch_cfg: "WatchConfig",
        step_id: str,
        original_query: str = "",
        source_video_path: str = "",
        resolved_video_path: str = "",
        time_base: str = "",
        temporal_hint_summary: str = "",
        store: Any = None,
        round_id: int = 0,
    ) -> "Evidence":
        """Query Qwen with video content (mirrors ``GeminiClient.infer_on_video``)."""
        from .main import (
            Evidence,
            _normalize_key_evidence_to_canonical_timebase,
        )

        if self.client is None:
            self.initialize_client()

        fps = watch_cfg.fps if watch_cfg.fps > 0 else None
        media_res = self._map_rate_to_media_res(watch_cfg.spatial_token_rate)

        meta_extractor = VideoMetadataExtractor(video_path)
        video_duration = meta_extractor.duration
        resolved_input_path = resolved_video_path or video_path
        source_input_path = source_video_path or video_path
        media_inputs: List[Dict[str, Any]] = []

        parts: List[List[Dict[str, Any]]] = []
        clip_paths: List[str] = []
        frames_used: List[Dict[str, Any]] = []

        # ---- PATH A: region + multiple regions ----
        if watch_cfg.load_mode == "region" and watch_cfg.regions:
            clip_dir = self.temp_clips_dir if self.temp_clips_dir else os.path.join(os.path.dirname(video_path), "temp_clips")

            for region_idx, (reg_start, reg_end) in enumerate(watch_cfg.regions):
                reg_start = max(0.0, float(reg_start))
                reg_end = min(duration_sec, float(reg_end))
                if reg_start >= reg_end:
                    if self.debug:
                        print(f"⚠️  Skipping invalid region {region_idx}: {reg_start:.1f}s >= {reg_end:.1f}s")
                    continue

                clip_path = create_video_clip(
                    video_path=video_path,
                    start_time=reg_start,
                    end_time=reg_end,
                    clip_name=(
                        f"step_{step_id}_region_{region_idx}_"
                        f"{int(round(reg_start * 1000))}_{int(round(reg_end * 1000))}"
                    ),
                    temp_dir=clip_dir,
                    debug=self.debug,
                )

                if clip_path:
                    clip_duration = reg_end - reg_start
                    part = self.create_video_part(
                        video_path=clip_path,
                        fps=fps,
                        start_offset=None,
                        end_offset=None,
                        media_resolution=media_res,
                        duration_sec=clip_duration,
                    )
                    parts.append(part)
                    clip_paths.append(clip_path)
                    frames_used.append({"start": reg_start, "end": reg_end, "fps": fps})
                    media_inputs.append({
                        "input_type": "clip",
                        "source_video_path": source_input_path,
                        "resolved_video_path": resolved_input_path,
                        "clip_path": clip_path,
                        "absolute_start_sec": reg_start,
                        "absolute_end_sec": reg_end,
                        "clip_time_base": "clip_local_seconds",
                    })
                    self.created_clips.append(clip_path)
                    if self.debug:
                        print(f"📹 Created clip {region_idx}: {clip_path} (range: {reg_start:.1f}s - {reg_end:.1f}s)")
                else:
                    if self.debug:
                        print(f"⚠️  Clip creation failed for region {region_idx}, using metadata offsets")
                    part = self.create_video_part(
                        video_path=video_path,
                        fps=fps,
                        start_offset=f"{reg_start}s" if reg_start > 0 else None,
                        end_offset=f"{reg_end}s" if reg_end > 0 else None,
                        media_resolution=media_res,
                    )
                    parts.append(part)
                    frames_used.append({"start": reg_start, "end": reg_end, "fps": fps})
                    media_inputs.append({
                        "input_type": "offset_window",
                        "source_video_path": source_input_path,
                        "resolved_video_path": resolved_input_path,
                        "absolute_start_sec": reg_start,
                        "absolute_end_sec": reg_end,
                        "clip_time_base": "raw_video_seconds",
                    })

            if clip_paths or parts:
                overall_start = min(r[0] for r in watch_cfg.regions)
                overall_end = max(r[1] for r in watch_cfg.regions)
                start_sec = overall_start
                end_sec = overall_end
            else:
                if self.debug:
                    print("⚠️  No valid regions created, falling back to single range")
                part = self.create_video_part(
                    video_path=video_path,
                    fps=fps,
                    start_offset=f"{start_sec}s" if start_sec > 0 else None,
                    end_offset=f"{end_sec}s" if end_sec > 0 else None,
                    media_resolution=media_res,
                )
                parts = [part]
                frames_used = [{"start": start_sec, "end": end_sec, "fps": fps}]
                media_inputs.append({
                    "input_type": "offset_window",
                    "source_video_path": source_input_path,
                    "resolved_video_path": resolved_input_path,
                    "absolute_start_sec": start_sec,
                    "absolute_end_sec": end_sec,
                    "clip_time_base": "raw_video_seconds",
                })

        # ---- PATH B: region + single region (start_sec / end_sec) ----
        elif watch_cfg.load_mode == "region" and start_sec is not None and end_sec is not None:
            if start_sec >= end_sec:
                if self.debug:
                    print(f"⚠️  Invalid time range for clip: {start_sec:.1f}s >= {end_sec:.1f}s, falling back to offsets")
                clip_path = None
            else:
                clip_dir = self.temp_clips_dir if self.temp_clips_dir else os.path.join(os.path.dirname(video_path), "temp_clips")
                clip_path = create_video_clip(
                    video_path=video_path,
                    start_time=start_sec,
                    end_time=end_sec,
                    temp_dir=clip_dir,
                    debug=self.debug,
                )

            if clip_path:
                clip_duration = end_sec - start_sec
                part = self.create_video_part(
                    video_path=clip_path,
                    fps=fps,
                    start_offset=None,
                    end_offset=None,
                    media_resolution=media_res,
                    duration_sec=clip_duration,
                )
                parts = [part]
                clip_paths = [clip_path]
                frames_used = [{"start": start_sec, "end": end_sec, "fps": fps}]
                media_inputs.append({
                    "input_type": "clip",
                    "source_video_path": source_input_path,
                    "resolved_video_path": resolved_input_path,
                    "clip_path": clip_path,
                    "absolute_start_sec": start_sec,
                    "absolute_end_sec": end_sec,
                    "clip_time_base": "clip_local_seconds",
                })
                self.created_clips.append(clip_path)
                if self.debug:
                    print(f"📹 Using clipped video: {clip_path} (original: {start_sec:.1f}s - {end_sec:.1f}s)")
            else:
                if self.debug:
                    print("⚠️  Clip creation failed, falling back to metadata offsets")
                part = self.create_video_part(
                    video_path=video_path,
                    fps=fps,
                    start_offset=f"{start_sec}s" if start_sec > 0 else None,
                    end_offset=f"{end_sec}s" if end_sec > 0 else None,
                    media_resolution=media_res,
                )
                parts = [part]
                frames_used = [{"start": start_sec, "end": end_sec, "fps": fps}]
                media_inputs.append({
                    "input_type": "offset_window",
                    "source_video_path": source_input_path,
                    "resolved_video_path": resolved_input_path,
                    "absolute_start_sec": start_sec,
                    "absolute_end_sec": end_sec,
                    "clip_time_base": "raw_video_seconds",
                })

        # ---- PATH C: uniform mode ----
        else:
            use_reencoded_uniform_clip = (
                watch_cfg.load_mode == "uniform"
                and isinstance(duration_sec, (int, float))
                and float(duration_sec) >= 900.0
            )
            if use_reencoded_uniform_clip:
                clip_dir = self.temp_clips_dir if self.temp_clips_dir else os.path.join(os.path.dirname(video_path), "temp_clips")
                if media_res == "low":
                    scale_width, video_bitrate, audio_bitrate, frame_rate, crf = 480, "220k", "24k", 6.0, 32
                elif media_res == "medium":
                    scale_width, video_bitrate, audio_bitrate, frame_rate, crf = 640, "320k", "32k", 8.0, 30
                else:
                    scale_width, video_bitrate, audio_bitrate, frame_rate, crf = 854, "550k", "48k", 10.0, 28

                clip_path = create_reencoded_video_clip(
                    video_path=video_path,
                    start_time=start_sec,
                    end_time=end_sec,
                    clip_name=(
                        f"step_{step_id}_uniform_reencoded_"
                        f"{int(round(start_sec * 1000))}_{int(round(end_sec * 1000))}"
                    ),
                    temp_dir=clip_dir,
                    scale_width=scale_width,
                    video_bitrate=video_bitrate,
                    audio_bitrate=audio_bitrate,
                    frame_rate=frame_rate,
                    crf=crf,
                    debug=self.debug,
                )
            else:
                clip_path = None

            if clip_path:
                part = self.create_video_part(
                    video_path=clip_path,
                    fps=fps,
                    start_offset=None,
                    end_offset=None,
                    media_resolution=media_res,
                    duration_sec=end_sec - start_sec,
                )
                parts = [part]
                clip_paths = [clip_path]
                frames_used = [{"start": start_sec, "end": end_sec, "fps": fps}]
                media_inputs.append({
                    "input_type": "reencoded_full_range_clip",
                    "source_video_path": source_input_path,
                    "resolved_video_path": resolved_input_path,
                    "clip_path": clip_path,
                    "absolute_start_sec": start_sec,
                    "absolute_end_sec": end_sec,
                    "clip_time_base": "raw_video_seconds",
                })
                self.created_clips.append(clip_path)
                if self.debug:
                    print(f"📹 Using re-encoded uniform clip: {clip_path} (range: {start_sec:.1f}s - {end_sec:.1f}s)")
            else:
                part = self.create_video_part(
                    video_path=video_path,
                    fps=fps,
                    start_offset=f"{start_sec}s" if start_sec > 0 else None,
                    end_offset=f"{end_sec}s" if end_sec > 0 else None,
                    media_resolution=media_res,
                )
                parts = [part]
                frames_used = [{"start": start_sec, "end": end_sec, "fps": fps}]
                media_inputs.append({
                    "input_type": "offset_window" if start_sec or end_sec else "full_video",
                    "source_video_path": source_input_path,
                    "resolved_video_path": resolved_input_path,
                    "absolute_start_sec": start_sec,
                    "absolute_end_sec": end_sec,
                    "clip_time_base": "raw_video_seconds",
                })

        # ---- Build prompt ----
        is_region = (len(clip_paths) > 0) or (watch_cfg.load_mode == "region")
        regions_for_prompt = None
        if is_region and watch_cfg.regions and len(watch_cfg.regions) > 1:
            regions_for_prompt = watch_cfg.regions

        prompt = PromptManager.get_inference_prompt(
            sub_query=sub_query,
            context=context,
            start_sec=start_sec,
            end_sec=end_sec,
            original_query=original_query,
            video_duration_sec=video_duration,
            is_region=is_region,
            regions=regions_for_prompt,
            media_inputs=media_inputs,
            time_base=time_base,
            temporal_hint_summary=temporal_hint_summary,
        )

        if self.debug:
            if len(parts) > 1:
                print(f"🎬 Querying video with {len(parts)} regions:")
                for i, (reg_start, reg_end) in enumerate(watch_cfg.regions):
                    print(f"   Region {i}: {reg_start:.1f}s - {reg_end:.1f}s")
            else:
                if clip_paths:
                    print(f"🎬 Querying video: {clip_paths[0]} (original range: {start_sec:.1f}s - {end_sec:.1f}s)")
                else:
                    print(f"🎬 Querying video: {video_path}")
                    print(f"   Time range: {start_sec:.1f}s - {end_sec:.1f}s")
            print(f"   FPS: {fps}, Resolution: {media_res}")

        # ---- Flatten video blocks and call API ----
        all_video_blocks: List[Dict[str, Any]] = []
        for part in parts:
            all_video_blocks.extend(part)

        response_text = self._call_video_api(self.execute_model, prompt, all_video_blocks)
        evidence_data = parse_json_response(response_text)

        if store is not None:
            store.append_role_trace("observer", round_id, prompt, response_text)

        # ---- Parse evidence ----
        if evidence_data and validate_against_schema(evidence_data, EVIDENCE_SCHEMA):
            summary = evidence_data.get("summary") or evidence_data.get("detailed_response", response_text)
            detailed_response = evidence_data.get("detailed_response") or evidence_data.get("summary", "")
            key_evidence = evidence_data.get("key_evidence", [])
            if self.debug:
                print(f"✅ Parsed structured evidence: {len(key_evidence)} items")
        else:
            detailed_response = self._extract_json_field(response_text, "detailed_response")
            if detailed_response and "description" in detailed_response:
                description_value = self._extract_json_field(detailed_response, "description")
                if description_value:
                    detailed_response = description_value
            summary = self._extract_json_field(response_text, "summary") or detailed_response or response_text
            key_evidence = self._extract_key_evidence(response_text)
            if detailed_response and detailed_response != response_text:
                if self.debug:
                    print(f"✅ Extracted fields from malformed JSON: {len(key_evidence)} items")
            else:
                summary = response_text
                detailed_response = response_text
                time_anchors = self._extract_timestamps(response_text)
                key_evidence = [
                    {"timestamp_start": max(0.0, t - 1.0), "timestamp_end": t + 1.0, "description": ""}
                    for t in time_anchors
                ]
                if self.debug:
                    print(f"⚠️  Using fallback parsing: {len(key_evidence)} items")

        # ---- Time-axis normalisation ----
        key_evidence, time_normalization = _normalize_key_evidence_to_canonical_timebase(
            key_evidence=key_evidence,
            media_inputs=media_inputs,
            duration_sec=duration_sec,
            debug=self.debug,
        )

        # ---- Round to full seconds ----
        try:
            raw_ranges: List[Tuple[float, float]] = []
            descs: List[str] = []
            for ev_item in key_evidence:
                if isinstance(ev_item, dict):
                    ts_start = ev_item.get("timestamp_start")
                    ts_end = ev_item.get("timestamp_end")
                    desc = ev_item.get("description", "")
                    if ts_start is not None and ts_end is not None:
                        raw_ranges.append((float(ts_start), float(ts_end)))
                        descs.append(str(desc))
            rounded = round_intervals_full_seconds(raw_ranges, duration=duration_sec)
            interval_to_desc: Dict[Tuple[int, int], str] = {}
            for idx, (rs, re_) in enumerate(rounded):
                chosen_desc = ""
                for jdx, (orig_s, orig_e) in enumerate(raw_ranges):
                    if (floor(orig_s), ceil(orig_e)) == (rs, re_):
                        cand = descs[jdx]
                        if cand and not interval_to_desc.get((rs, re_)):
                            chosen_desc = cand
                            break
                interval_to_desc[(rs, re_)] = chosen_desc
            key_evidence = [
                {"timestamp_start": int(rs), "timestamp_end": int(re_), "description": interval_to_desc.get((rs, re_), "")}
                for (rs, re_) in rounded
            ]
        except Exception:
            pass

        # ---- Build metadata ----
        model_call_metadata: Dict[str, Any] = {
            "model": self.execute_model,
            "fps": fps,
            "media_resolution": media_res,
            "prompt_version": "v2_structured",
            "source_video_path": source_input_path,
            "resolved_video_path": resolved_input_path,
            "time_base": time_base or "raw_video_seconds",
            "media_inputs": media_inputs,
            "time_normalization": time_normalization,
        }
        if len(frames_used) > 1:
            model_call_metadata["regions"] = frames_used
            model_call_metadata["num_regions"] = len(frames_used)
        else:
            if frames_used:
                model_call_metadata["start_offset"] = f"{frames_used[0]['start']}s" if frames_used[0]["start"] > 0 else None
                model_call_metadata["end_offset"] = f"{frames_used[0]['end']}s" if frames_used[0]["end"] > 0 else None
        if clip_paths:
            model_call_metadata["clip_paths"] = clip_paths

        return Evidence(
            detailed_response=detailed_response,
            key_evidence=key_evidence,
            reasoning=summary,
            frames_used=frames_used,
            model_call=model_call_metadata,
            timestamp=now_iso(),
            round_id=0,  # Will be set by Controller
        )

    # ------------------------------------------------------------------
    # synthesize_final_answer()
    # ------------------------------------------------------------------

    def synthesize_final_answer(
        self,
        plan: "PlanSpec",
        bb: "Blackboard",
        store: Any = None,
        round_id: int = 0,
    ) -> Dict[str, Any]:
        """Synthesize a final answer from all evidence (mirrors ``GeminiClient.synthesize_final_answer``)."""
        from .main import normalize_final_answer_output

        if self.client is None:
            self.initialize_client()

        options = bb.meta.get("options", None)
        options_list = options if options else []

        prompt = PromptManager.get_synthesis_prompt(
            original_query=plan.query,
            all_evidence=bb.summary_text(),
            video_duration=bb.duration_sec or 0.0,
            options=options_list,
            time_base=str(bb.meta.get("time_base", "") or ""),
            temporal_hint_summary=str(bb.meta.get("temporal_hint_summary", "") or ""),
        )

        if self.debug:
            print("🔄 Synthesizing final answer...")
            if options_list:
                print(f"📝 MCQ format with {len(options_list)} options")
            else:
                print("📝 Open-ended format")

        response_text = self._call_text_api(self.plan_replan_model, prompt)

        if store is not None:
            store.append_role_trace("synthesizer", round_id, prompt, response_text)

        answer_data = parse_json_response(response_text)

        query_confidence = bb.query_confidence if bb.query_confidence is not None else 0.5
        normalized = normalize_final_answer_output(
            answer_data=answer_data,
            response_text=response_text,
            query_confidence=query_confidence,
            options=options_list,
        )
        if self.debug:
            print(f"✅ Parsed final answer in {'MCQ' if options_list else 'open-ended'} format")
        return normalized

    # ------------------------------------------------------------------
    # Fallback / extraction helpers (mirror GeminiClient)
    # ------------------------------------------------------------------

    def _get_fallback_plan(self, query: str) -> "PlanSpec":
        from .main import SpatialTokenRate, WatchConfig, PlanSpec
        watch = WatchConfig(load_mode="uniform", fps=0.5, spatial_token_rate=SpatialTokenRate.low)
        return PlanSpec(
            plan_version="v1",
            query=query,
            watch=watch,
            description="Uniform scan to gather evidence",
        )

    def _extract_timestamps(self, text: str) -> List[float]:
        """Extract timestamps from model response text."""
        patterns = [
            r'(\d+\.?\d*)\s*(?:seconds?|secs?|s\b)',
            r'at\s+(\d+\.?\d*)',
            r'(\d+):(\d+)',
        ]
        timestamps: List[float] = []
        for pattern in patterns:
            matches = re.finditer(pattern, text, re.IGNORECASE)
            for match in matches:
                if ':' in match.group(0):
                    mins, secs = match.groups()
                    timestamps.append(float(mins) * 60 + float(secs))
                else:
                    timestamps.append(float(match.group(1)))
        return sorted(list(set(timestamps)))

    def _extract_confidence(self, text: str) -> float:
        """Extract confidence score from model response text."""
        patterns = [
            r'confidence[:\s]+(\d+\.?\d*)',
            r'(\d+\.?\d*)\s+confidence',
        ]
        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                conf = float(match.group(1))
                return conf if conf <= 1.0 else conf / 100.0
        return 0.5

    def _extract_json_field(self, text: str, field_name: str) -> str:
        """Extract a specific field value from malformed JSON using regex."""
        nested_pattern = f'"{field_name}"' + r'\s*:\s*\{' + r'\s*"description"\s*:\s*"([^"\\]*(?:\\.[^"\\]*)*)"'
        direct_pattern = rf'"{field_name}"\s*:\s*"([^"\\]*(?:\\.[^"\\]*)*)"'
        single_quote_pattern = rf'"{field_name}"\s*:\s*\'([^\'\\]*(?:\\.[^\'\\]*)*)\''
        nested_direct_pattern = f'"{field_name}"' + r'[^}]*"description"\s*:\s*"([^"\\]*(?:\\.[^"\\]*)*)"'

        patterns = [
            nested_pattern,
            direct_pattern,
            single_quote_pattern,
            nested_direct_pattern,
        ]
        for pattern in patterns:
            match = re.search(pattern, text, re.DOTALL)
            if match:
                value = match.group(1)
                value = value.replace('\\"', '"').replace("\\'", "'").replace('\\n', '\n').replace('\\t', '\t')
                value = value.replace('\\', '')
                return value
        return ""

    def _extract_key_evidence(self, text: str) -> List[Dict[str, Any]]:
        """Extract ``key_evidence`` array from malformed JSON."""
        key_evidence: List[Dict[str, Any]] = []

        pattern_range = (
            r'"timestamp_start"\s*:\s*(\d+\.?\d*)\s*,\s*'
            r'"timestamp_end"\s*:\s*(\d+\.?\d*)\s*,\s*'
            r'"description"\s*:\s*"([^"\\]*(?:\\.[^"\\]*)*)"'
        )
        for match in re.finditer(pattern_range, text, re.DOTALL):
            ts_start = float(match.group(1))
            ts_end = float(match.group(2))
            description = match.group(3)
            description = description.replace('\\"', '"').replace('\\n', '\n').replace('\\t', '\t')
            key_evidence.append({
                "timestamp_start": ts_start,
                "timestamp_end": ts_end,
                "description": description,
            })

        if not key_evidence:
            pattern_legacy = (
                r'"timestamp_sec"\s*:\s*(\d+\.?\d*)\s*,\s*'
                r'"description"\s*:\s*"([^"\\]*(?:\\.[^"\\]*)*)"'
            )
            for match in re.finditer(pattern_legacy, text, re.DOTALL):
                timestamp = float(match.group(1))
                description = match.group(2)
                description = description.replace('\\"', '"').replace('\\n', '\n').replace('\\t', '\t')
                key_evidence.append({
                    "timestamp_start": max(0.0, timestamp - 1.0),
                    "timestamp_end": timestamp + 1.0,
                    "description": description,
                })

        return key_evidence


# ------------------------------------------------------------------
# Factory
# ------------------------------------------------------------------

def create_client(cfg: Any) -> Any:
    """Return ``GeminiClient`` or ``QwenClient`` based on ``cfg.backend``."""
    if getattr(cfg, "backend", "gemini") == "qwen":
        return QwenClient(
            model=cfg.qwen_model,
            plan_replan_model=cfg.get_qwen_plan_model(),
            execute_model=cfg.get_qwen_execute_model(),
            api_key=(cfg.qwen_api_key or "").strip() or None,
            base_url=cfg.get_qwen_base_url() or None,
            debug=cfg.debug,
            max_frame_low=cfg.max_frame_low,
            max_frame_medium=cfg.max_frame_medium,
            max_frame_high=cfg.max_frame_high,
            prefer_compressed=cfg.prefer_compressed,
            keep_temp_clips=cfg.keep_temp_clips,
            qwen_video_mode=getattr(cfg, "qwen_video_mode", "video"),
        )
    else:
        from .main import GeminiClient

        api_key_val = (cfg.api_key or "").strip() or None
        return GeminiClient(
            model=cfg.model,
            plan_replan_model=cfg.get_plan_replan_model(),
            execute_model=cfg.get_execute_model(),
            project=cfg.project,
            location=cfg.get_random_location(),
            api_key=api_key_val,
            base_url=(cfg.base_url or "").strip() or None,
            max_frame_low=cfg.max_frame_low,
            max_frame_medium=cfg.max_frame_medium,
            max_frame_high=cfg.max_frame_high,
            prefer_compressed=cfg.prefer_compressed,
            keep_temp_clips=cfg.keep_temp_clips,
            debug=cfg.debug,
        )
