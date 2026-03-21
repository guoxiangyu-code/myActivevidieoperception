#!/usr/bin/env python3
"""
Prototype AVP-style first-question runner using local Qwen3.5-9B.

This script is intentionally narrow in scope:
- runs only one QA sample at a time
- preserves the planner -> observer -> reflector -> synthesizer flow
- writes raw prompt/response traces for every role
- adds a lightweight correctness judge for the final answer
"""

from __future__ import annotations

import argparse
import gc
import inspect
import json
import math
import os
import re
import string
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import cv2
import torch
from PIL import Image
from transformers import AutoProcessor, Qwen3_5ForConditionalGeneration

from .prompt import (
    EVIDENCE_SCHEMA,
    PLAN_SCHEMA,
    REFLECTION_SCHEMA,
    PromptManager,
    parse_json_response,
    validate_against_schema,
)


QA_SCHEMA = {
    "type": "object",
    "properties": {
        "answer": {"type": "string"},
        "confidence": {"type": "number"},
        "reasoning": {"type": "string"},
    },
    "required": ["answer", "confidence", "reasoning"],
}


@dataclass
class QuestionRecord:
    id: str
    video_id: str
    task_type: str
    question: str
    answer: str
    cot_reference: str
    path: str
    duration: float


def now_stamp() -> str:
    return time.strftime("%Y%m%d_%H%M%S", time.localtime())


def normalize_text(text: str) -> str:
    text = (text or "").strip().lower()
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    table = str.maketrans("", "", string.punctuation + "，。！？；：、“”‘’（）()[]{}<>")
    text = text.translate(table)
    text = re.sub(r"\s+", "", text)
    return text


def infer_video_path(video_id: str, dataset_root: Path) -> Path:
    folder = "_".join(video_id.split("_")[:3])
    return dataset_root / folder / f"{video_id}.mp4"


def compute_duration(video_path: Path) -> float:
    cap = cv2.VideoCapture(str(video_path))
    fps = cap.get(cv2.CAP_PROP_FPS) or 0.0
    frames = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0.0
    cap.release()
    if fps <= 0:
        return 0.0
    return float(frames / fps)


def load_question_by_index(
    qa_path: Path,
    dataset_root: Path,
    index: int,
) -> QuestionRecord:
    data = json.loads(qa_path.read_text())
    sample = data[index]
    video_path = infer_video_path(sample["video_id"], dataset_root)
    return QuestionRecord(
        id=sample["id"],
        video_id=sample["video_id"],
        task_type=sample["task_type"],
        question=sample["question"],
        answer=sample["answer"],
        cot_reference=sample["CoT"],
        path=str(video_path.resolve()),
        duration=compute_duration(video_path),
    )


def make_run_dir(base_dir: Path, question_id: str) -> Path:
    run_dir = base_dir / f"{question_id}_{now_stamp()}"
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def write_json(path: Path, obj: Any) -> None:
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2))


def write_text(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def build_open_qa_synthesis_prompt(
    question: str,
    all_evidence: str,
    video_duration: float,
) -> str:
    return f"""You are synthesizing the final answer to an open-ended question about a video.

Question:
{question}

Video Duration:
{video_duration:.1f} seconds

Evidence from previous rounds:
{all_evidence}

Instructions:
- Answer the question directly.
- Use only the evidence provided above.
- If the evidence is weak or insufficient, say so explicitly in the reasoning and keep the confidence low.
- Do not pretend the answer is known when the event or supporting evidence has not been observed.
- Return valid JSON only.

JSON schema:
{json.dumps(QA_SCHEMA, ensure_ascii=False, indent=2)}
"""


def build_correctness_judge_prompt(
    question: str,
    predicted_answer: str,
    reference_answer: str,
    cot_reference: str,
) -> str:
    return f"""You are judging whether a predicted answer to a video QA question should be considered correct.

Question:
{question}

Predicted Answer:
{predicted_answer}

Reference Answer:
{reference_answer}

Reference Chain-of-Thought (for judging context only, not as a required wording target):
{cot_reference}

Instructions:
- Judge semantic correctness, not only surface wording.
- If the predicted answer captures the same core meaning as the reference answer, mark it correct.
- Your output format is flexible, but you MUST clearly state the verdict as either:
  - Correct
  - Incorrect
- Optionally include a short reason.
- Do NOT output chain-of-thought or detailed analysis.
- Keep output concise (1-2 lines preferred).

Preferred concise format:
Verdict: Correct
Reason: ...

or

Verdict: Incorrect
Reason: ...
"""


def plan_to_summary(plan_data: Dict[str, Any]) -> str:
    step = plan_data["steps"][0]
    return json.dumps(
        {
            "description": step.get("description", ""),
            "load_mode": step.get("load_mode", ""),
            "fps": step.get("fps", 0),
            "spatial_token_rate": step.get("spatial_token_rate", ""),
            "regions": step.get("regions", []),
            "completion_criteria": plan_data.get("completion_criteria", ""),
        },
        ensure_ascii=False,
        indent=2,
    )


def truncate_text(text: str, max_chars: int = 600) -> str:
    text = (text or "").strip()
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3] + "..."


def merge_intervals(
    intervals: Sequence[Tuple[float, float]],
    duration: Optional[float] = None,
) -> List[Tuple[float, float]]:
    normalized: List[Tuple[float, float]] = []
    for start, end in intervals:
        start = float(start)
        end = float(end)
        if duration is not None:
            start = max(0.0, min(start, duration))
            end = max(0.0, min(end, duration))
        if end <= start:
            continue
        normalized.append((start, end))
    if not normalized:
        return []
    normalized.sort(key=lambda item: (item[0], item[1]))
    merged: List[List[float]] = [[normalized[0][0], normalized[0][1]]]
    for start, end in normalized[1:]:
        last_start, last_end = merged[-1]
        if start <= last_end:
            merged[-1][1] = max(last_end, end)
        else:
            merged.append([start, end])
    return [(float(start), float(end)) for start, end in merged]


def intervals_to_text(intervals: Sequence[Tuple[float, float]]) -> str:
    if not intervals:
        return "None"
    return ", ".join(f"[{start:.1f}, {end:.1f}]s" for start, end in intervals)


def coverage_ratio(intervals: Sequence[Tuple[float, float]], duration: float) -> float:
    if duration <= 0:
        return 0.0
    merged = merge_intervals(intervals, duration)
    covered = sum(end - start for start, end in merged)
    return min(1.0, max(0.0, covered / duration))


def interval_overlap_seconds(span: Tuple[float, float], intervals: Sequence[Tuple[float, float]]) -> float:
    start, end = span
    if end <= start:
        return 0.0
    overlap = 0.0
    for other_start, other_end in merge_intervals(intervals):
        overlap += max(0.0, min(end, other_end) - max(start, other_start))
    return overlap


def next_uncovered_window(
    duration: float,
    observed_intervals: Sequence[Tuple[float, float]],
    window_sec: float,
    start_hint: float = 0.0,
) -> Tuple[float, float]:
    if duration <= 0:
        return 0.0, 1.0
    window_sec = max(1.0, min(float(window_sec), duration))
    merged = merge_intervals(observed_intervals, duration)
    candidates = [max(0.0, min(float(start_hint), duration)), 0.0]

    for candidate_start in candidates:
        cursor = candidate_start
        for start, end in merged:
            if end <= cursor:
                continue
            if start > cursor:
                return cursor, min(duration, cursor + window_sec)
            cursor = max(cursor, end)
            if cursor >= duration:
                break
        if cursor < duration:
            return cursor, min(duration, cursor + window_sec)

    return max(0.0, duration - window_sec), duration


def compute_sampling_span(
    question: str,
    duration: float,
    load_mode: str,
    regions: Sequence[Sequence[float]],
    observed_intervals: Optional[Sequence[Tuple[float, float]]] = None,
    uniform_window_sec: float = 180.0,
) -> Tuple[float, float, str]:
    observed_intervals = observed_intervals or []
    if load_mode == "region" and regions:
        starts = [float(r[0]) for r in regions]
        ends = [float(r[1]) for r in regions]
        return max(0.0, min(starts)), min(duration, max(ends)), "planned-region"

    beginning_markers = ("刚开始", "开局", "一开始", "比赛开始", "opening", "beginning")
    ending_markers = ("最后", "结尾", "末尾", "结束", "ending", "end")

    start_hint = 0.0
    if not observed_intervals and any(marker in question for marker in ending_markers):
        start_hint = max(0.0, duration - min(duration, uniform_window_sec))

    if not observed_intervals and any(marker in question for marker in beginning_markers):
        start_hint = 0.0

    start_sec, end_sec = next_uncovered_window(
        duration=duration,
        observed_intervals=observed_intervals,
        window_sec=uniform_window_sec,
        start_hint=start_hint,
    )
    return start_sec, end_sec, "uniform-window"


def resolve_observation_span(
    question: str,
    duration: float,
    load_mode: str,
    regions: Sequence[Sequence[float]],
    observed_intervals: Sequence[Tuple[float, float]],
    uniform_window_sec: float,
) -> Tuple[float, float, str]:
    start_sec, end_sec, strategy = compute_sampling_span(
        question=question,
        duration=duration,
        load_mode=load_mode,
        regions=regions,
        observed_intervals=observed_intervals,
        uniform_window_sec=uniform_window_sec,
    )

    # Preserve intentional region revisits/reinterpretation:
    # when planner explicitly chooses region mode with concrete regions,
    # do not force-overwrite it with uncovered-window fallback.
    if load_mode == "region" and regions:
        return start_sec, end_sec, strategy

    span_duration = max(1.0, end_sec - start_sec)
    overlap_ratio = interval_overlap_seconds((start_sec, end_sec), observed_intervals) / span_duration
    if observed_intervals and overlap_ratio >= 0.8 and coverage_ratio(observed_intervals, duration) < 0.99:
        fallback_start, fallback_end = next_uncovered_window(
            duration=duration,
            observed_intervals=observed_intervals,
            window_sec=uniform_window_sec,
            start_hint=end_sec,
        )
        if (fallback_start, fallback_end) != (start_sec, end_sec):
            return fallback_start, fallback_end, "overlap-guard-uniform-window"

    return start_sec, end_sec, strategy


def build_replanning_evidence_summary(
    round_records: Sequence[Dict[str, Any]],
    watched_intervals: Sequence[Tuple[float, float]],
    duration: float,
) -> str:
    lines = [
        f"Watched intervals: {intervals_to_text(watched_intervals)}",
        f"Coverage ratio: {coverage_ratio(watched_intervals, duration):.2f}",
    ]
    for record in round_records:
        lines.append(f"Round {record['round_idx']}:")
        lines.append(f"- Plan summary: {record['plan_summary']}")
        lines.append(
            f"- Executed span: {record['start_sec']:.1f}s to {record['end_sec']:.1f}s "
            f"({record['sampling_strategy']})"
        )
        lines.append(
            f"- Evidence summary: {truncate_text(record['evidence_data'].get('detailed_response', ''), 500)}"
        )
        lines.append(f"- Key evidence count: {len(record['evidence_data'].get('key_evidence', []) or [])}")
        synthesis = record.get("synthesis_data")
        if synthesis:
            lines.append(
                f"- Provisional answer (confidence={float(synthesis.get('confidence', 0.0)):.2f}): "
                f"{truncate_text(synthesis.get('answer', ''), 220)}"
            )
        reflection = record.get("reflection_data")
        if reflection:
            lines.append(
                f"- Reflection: sufficient={reflection.get('sufficient')}, "
                f"answerability_confidence={float(reflection.get('answerability_confidence', 0.0)):.2f}, "
                f"next_search_strategy={reflection.get('next_search_strategy', '')}"
            )
            lines.append(
                f"- Missing information: {truncate_text(reflection.get('missing_information', ''), 260)}"
            )
    return "\n".join(lines)


def build_all_evidence_summary(round_records: Sequence[Dict[str, Any]]) -> str:
    blocks: List[str] = []
    for record in round_records:
        blocks.append(
            json.dumps(
                {
                    "round": record["round_idx"],
                    "observed_span": [round(record["start_sec"], 3), round(record["end_sec"], 3)],
                    "sampling_strategy": record["sampling_strategy"],
                    "evidence": {
                        "detailed_response": truncate_text(
                            record["evidence_data"].get("detailed_response", ""),
                            1200,
                        ),
                        "key_evidence": record["evidence_data"].get("key_evidence", []),
                        "reasoning": truncate_text(record["evidence_data"].get("reasoning", ""), 800),
                    },
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    return "\n\n".join(blocks)


def build_observer_context(round_records: Sequence[Dict[str, Any]]) -> str:
    if not round_records:
        return "None (first step)"
    recent_records = list(round_records[-2:])
    lines = ["Previous rounds summary:"]
    for record in recent_records:
        lines.append(
            f"- Round {record['round_idx']}: watched {record['start_sec']:.1f}-{record['end_sec']:.1f}s; "
            f"provisional confidence={float(record['synthesis_data'].get('confidence', 0.0)):.2f}; "
            f"reflection sufficient={record['reflection_data'].get('sufficient')}; "
            f"missing={truncate_text(record['reflection_data'].get('missing_information', ''), 180)}"
        )
    return "\n".join(lines)


def build_observer_chunk_prompt(
    question: str,
    sub_query: str,
    context: str,
    start_sec: float,
    end_sec: float,
    video_duration: float,
) -> str:
    return f"""You are the Observer role in a video QA system.

Question:
{question}

Sub-query for this observation:
{sub_query}

Video duration:
{video_duration:.1f} seconds

Current observed segment:
[{start_sec:.1f}, {end_sec:.1f}] seconds

Previous-round context:
{context}

Instructions:
- Analyze only the provided frames for this current segment.
- If the target event is not visible in this segment, state that explicitly.
- Do not claim observations that are not visible.
- Keep the response concise:
  - `detailed_response`: <= 120 words
  - `reasoning`: <= 80 words
  - `key_evidence`: <= 3 items
- Any evidence timestamps must stay within [{start_sec:.1f}, {end_sec:.1f}].

Output requirements:
- Return exactly one JSON object and nothing else.
- No markdown, no code fences, no bullet list, no extra prose.

JSON schema:
{json.dumps(EVIDENCE_SCHEMA, ensure_ascii=False, indent=2)}
"""



def sample_frames(
    video_path: Path,
    start_sec: float,
    end_sec: float,
    fps: float,
    max_frames: int = 0,
    image_max_side: int = 0,
) -> Tuple[List[Image.Image], List[Dict[str, Any]]]:
    if end_sec <= start_sec:
        end_sec = start_sec + 1.0
    fps = max(0.1, float(fps))
    duration = end_sec - start_sec
    wanted = max(1, int(math.ceil(duration * fps)))
    frame_count = wanted if max_frames <= 0 else min(max_frames, wanted)
    if frame_count == 1:
        timestamps = [start_sec]
    else:
        step = duration / (frame_count - 1)
        timestamps = [start_sec + i * step for i in range(frame_count)]

    cap = cv2.VideoCapture(str(video_path))
    images: List[Image.Image] = []
    metadata: List[Dict[str, Any]] = []
    for idx, ts in enumerate(timestamps):
        cap.set(cv2.CAP_PROP_POS_MSEC, ts * 1000.0)
        ok, frame = cap.read()
        if not ok:
            continue
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        original_height, original_width = rgb.shape[:2]
        image = Image.fromarray(rgb)
        if image_max_side > 0:
            image.thumbnail((image_max_side, image_max_side), Image.Resampling.LANCZOS)
        images.append(image)
        delivered_width, delivered_height = image.size
        metadata.append(
            {
                "index": idx,
                "timestamp_sec": round(ts, 3),
                "original_width": int(original_width),
                "original_height": int(original_height),
                "delivered_width": int(delivered_width),
                "delivered_height": int(delivered_height),
            }
        )
    cap.release()
    return images, metadata


def chunk_sequence(items: Sequence[Any], chunk_size: int) -> List[List[Any]]:
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    return [list(items[i:i + chunk_size]) for i in range(0, len(items), chunk_size)]


def evenly_spaced_indices(total_items: int, keep_items: int) -> List[int]:
    if total_items <= 0:
        return []
    if keep_items <= 0 or keep_items >= total_items:
        return list(range(total_items))
    if keep_items == 1:
        return [0]
    return [
        min(total_items - 1, math.floor(i * (total_items - 1) / (keep_items - 1)))
        for i in range(keep_items)
    ]


def limit_chunk_frames(
    images: Sequence[Image.Image],
    metadata: Sequence[Dict[str, Any]],
    max_frames: int,
) -> Tuple[List[Image.Image], List[Dict[str, Any]], int]:
    source_frame_count = len(metadata)
    if len(images) != source_frame_count:
        raise ValueError("Frame limiter received mismatched image and metadata lengths.")
    if max_frames <= 0 or source_frame_count <= max_frames:
        return list(images), list(metadata), source_frame_count

    keep_indices = evenly_spaced_indices(source_frame_count, max_frames)
    return (
        [images[index] for index in keep_indices],
        [metadata[index] for index in keep_indices],
        source_frame_count,
    )


def build_observer_chunks(
    images: Sequence[Image.Image],
    metadata: Sequence[Dict[str, Any]],
    segment_start_sec: float,
    segment_end_sec: float,
    window_sec: float,
    legacy_chunk_size: int,
    max_frames_per_chunk: int,
) -> List[Dict[str, Any]]:
    if len(images) != len(metadata):
        raise ValueError("Observer chunk builder received mismatched image and metadata lengths.")
    if not metadata:
        return []

    if window_sec <= 0:
        chunk_size = legacy_chunk_size if legacy_chunk_size > 0 else len(images)
        image_chunks = chunk_sequence(images, chunk_size)
        metadata_chunks = chunk_sequence(metadata, chunk_size)
        built_chunks: List[Dict[str, Any]] = []
        for chunk_id, (chunk_images, chunk_meta) in enumerate(zip(image_chunks, metadata_chunks), start=1):
            chunk_start_sec = float(chunk_meta[0]["timestamp_sec"])
            chunk_end_sec = float(chunk_meta[-1]["timestamp_sec"])
            if chunk_end_sec <= chunk_start_sec:
                chunk_end_sec = chunk_start_sec + 1.0
            limited_images, limited_meta, source_frame_count = limit_chunk_frames(
                chunk_images,
                chunk_meta,
                max_frames=max_frames_per_chunk,
            )
            built_chunks.append(
                {
                    "chunk_id": chunk_id,
                    "start_sec": chunk_start_sec,
                    "end_sec": chunk_end_sec,
                    "mode": "frame_count_fallback",
                    "source_frame_count": source_frame_count,
                    "images": limited_images,
                    "metadata": limited_meta,
                }
            )
        return built_chunks

    segment_duration = max(1.0, float(segment_end_sec) - float(segment_start_sec))
    raw_chunks: Dict[int, Dict[str, Any]] = {}
    max_offset = max(0.0, segment_duration - 1e-6)

    # Group frames by fixed temporal windows so each observer call covers
    # a contiguous k-second span instead of an arbitrary count of adjacent frames.
    for image, item in zip(images, metadata):
        timestamp_sec = float(item["timestamp_sec"])
        offset_sec = min(max(0.0, timestamp_sec - float(segment_start_sec)), max_offset)
        chunk_index = int(offset_sec // window_sec)
        chunk_start_sec = float(segment_start_sec) + chunk_index * window_sec
        chunk_end_sec = min(float(segment_end_sec), chunk_start_sec + window_sec)
        chunk = raw_chunks.setdefault(
            chunk_index,
            {
                "start_sec": chunk_start_sec,
                "end_sec": chunk_end_sec,
                "mode": "temporal_window",
                "images": [],
                "metadata": [],
            },
        )
        chunk["images"].append(image)
        chunk["metadata"].append(item)

    built_chunks = []
    for chunk_id, chunk_index in enumerate(sorted(raw_chunks), start=1):
        chunk = raw_chunks[chunk_index]
        limited_images, limited_meta, source_frame_count = limit_chunk_frames(
            chunk["images"],
            chunk["metadata"],
            max_frames=max_frames_per_chunk,
        )
        built_chunks.append(
            {
                "chunk_id": chunk_id,
                "start_sec": chunk["start_sec"],
                "end_sec": chunk["end_sec"],
                "mode": chunk["mode"],
                "source_frame_count": source_frame_count,
                "images": limited_images,
                "metadata": limited_meta,
            }
        )
    return built_chunks


def aggregate_evidence_chunks(chunk_records: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    detailed_parts: List[str] = []
    reasoning_parts: List[str] = []
    merged_evidence: List[Dict[str, Any]] = []
    seen_evidence: set[Tuple[float, float, str]] = set()

    for record in chunk_records:
        chunk_id = record["chunk_id"]
        chunk_start = record["start_sec"]
        chunk_end = record["end_sec"]
        parsed = record["parsed"]
        detailed = (parsed.get("detailed_response") or "").strip()
        reasoning = (parsed.get("reasoning") or "").strip()
        if detailed:
            detailed_parts.append(f"[chunk {chunk_id:02d} | {chunk_start:.1f}-{chunk_end:.1f}s] {detailed}")
        if reasoning:
            reasoning_parts.append(f"[chunk {chunk_id:02d}] {reasoning}")
        for item in parsed.get("key_evidence", []) or []:
            key = (
                float(item.get("timestamp_start", 0.0)),
                float(item.get("timestamp_end", 0.0)),
                str(item.get("description", "")),
            )
            if key in seen_evidence:
                continue
            seen_evidence.add(key)
            merged_evidence.append(
                {
                    "timestamp_start": key[0],
                    "timestamp_end": key[1],
                    "description": key[2],
                }
            )

    merged_evidence.sort(key=lambda item: (item["timestamp_start"], item["timestamp_end"], item["description"]))
    return {
        "detailed_response": "\n\n".join(detailed_parts),
        "key_evidence": merged_evidence,
        "reasoning": "\n\n".join(reasoning_parts),
    }


class QwenChatRunner:
    def __init__(self, model_path: str):
        self.model_path = model_path
        self.processor = AutoProcessor.from_pretrained(model_path, use_fast=False)
        max_memory: Optional[Dict[Any, str]] = None
        device_map: str = "auto"
        if torch.cuda.is_available():
            gpu_count = torch.cuda.device_count()
            if gpu_count >= 2:
                device_map = "balanced"
                max_memory = {gpu_idx: "18GiB" for gpu_idx in range(gpu_count)}
                max_memory["cpu"] = "48GiB"
        self.model = Qwen3_5ForConditionalGeneration.from_pretrained(
            model_path,
            torch_dtype=torch.float16,
            device_map=device_map,
            max_memory=max_memory,
            low_cpu_mem_usage=True,
        )

    def _prepare_inputs(self, prompt: str, images: Optional[Sequence[Image.Image]]) -> Dict[str, Any]:
        content: List[Dict[str, Any]] = []
        if images:
            for image in images:
                content.append({"type": "image", "image": image})
        content.append({"type": "text", "text": prompt})
        messages = [{"role": "user", "content": content}]

        base_kwargs: Dict[str, Any] = {
            "tokenize": True,
            "add_generation_prompt": True,
            "return_dict": True,
            "return_tensors": "pt",
        }
        try:
            sig = inspect.signature(self.processor.apply_chat_template)
            if "enable_thinking" in sig.parameters:
                base_kwargs["enable_thinking"] = False
        except Exception:
            # Fallback to default kwargs when signature inspection is unavailable.
            pass
        try:
            inputs = self.processor.apply_chat_template(
                messages,
                **base_kwargs,
            )
        except Exception:
            text_messages = [{"role": "user", "content": [{"type": "text", "text": prompt}]}]
            fallback_kwargs: Dict[str, Any] = {
                "tokenize": False,
                "add_generation_prompt": True,
            }
            try:
                sig = inspect.signature(self.processor.apply_chat_template)
                if "enable_thinking" in sig.parameters:
                    fallback_kwargs["enable_thinking"] = False
            except Exception:
                pass
            text_prompt = self.processor.apply_chat_template(text_messages, **fallback_kwargs)
            if images:
                inputs = self.processor(text=[text_prompt], images=list(images), return_tensors="pt", padding=True)
            else:
                inputs = self.processor(text=[text_prompt], return_tensors="pt", padding=True)
        return inputs

    def generate(self, prompt: str, images: Optional[Sequence[Image.Image]] = None, max_new_tokens: int = 1024) -> str:
        inputs = self._prepare_inputs(prompt, images)
        target_device = next(self.model.parameters()).device
        inputs = inputs.to(target_device)
        try:
            with torch.inference_mode():
                outputs = self.model.generate(
                    **inputs,
                    max_new_tokens=max_new_tokens,
                    do_sample=False,
                )
            trimmed = [
                out_ids[len(in_ids):]
                for in_ids, out_ids in zip(inputs["input_ids"], outputs)
            ]
            text = self.processor.batch_decode(
                trimmed,
                skip_special_tokens=True,
                clean_up_tokenization_spaces=False,
            )[0]
            return text.strip()
        finally:
            del inputs
            if "outputs" in locals():
                del outputs
            gc.collect()
            if torch.cuda.is_available():
                torch.cuda.empty_cache()


_RUNNER_CACHE: Dict[str, QwenChatRunner] = {}


def get_cached_runner(model_path: str) -> QwenChatRunner:
    cached = _RUNNER_CACHE.get(model_path)
    if cached is not None:
        return cached
    runner = QwenChatRunner(model_path)
    _RUNNER_CACHE[model_path] = runner
    return runner


def fallback_evidence_from_unstructured_raw(raw: str) -> Dict[str, Any]:
    cleaned = re.sub(r"</?think>", " ", raw, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if not cleaned:
        cleaned = "Observer response was empty after cleanup."
    detailed = truncate_text(cleaned, 1200)
    reasoning = (
        "Observer response was not valid JSON after retries. "
        "A deterministic fallback converted unstructured text to schema fields."
    )
    return {
        "detailed_response": detailed,
        "key_evidence": [],
        "reasoning": reasoning,
    }


def fallback_reflection_from_unstructured_raw(raw: str) -> Dict[str, Any]:
    text = (raw or "").strip()
    compact = re.sub(r"\s+", " ", re.sub(r"</?think>", " ", text, flags=re.IGNORECASE)).strip()

    def parse_bool_key(key: str, default: bool) -> bool:
        pattern = rf"[\"']?{re.escape(key)}[\"']?\s*[:=]\s*(true|false)"
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return match.group(1).lower() == "true"
        return default

    def parse_float_key(key: str, default: float) -> float:
        pattern = rf"[\"']?{re.escape(key)}[\"']?\s*[:=]\s*([-+]?\d*\.?\d+)"
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            try:
                return max(0.0, min(1.0, float(match.group(1))))
            except ValueError:
                return default
        return default

    def parse_string_key(key: str) -> str:
        quoted = re.search(rf"[\"']?{re.escape(key)}[\"']?\s*[:=]\s*[\"']([^\"']+)[\"']", text, flags=re.IGNORECASE)
        if quoted:
            return quoted.group(1).strip()
        plain = re.search(rf"[\"']?{re.escape(key)}[\"']?\s*[:=]\s*([^\n\r,}}]+)", text, flags=re.IGNORECASE)
        if plain:
            return plain.group(1).strip().strip('"').strip("'")
        return ""

    sufficient_default = False
    if re.search(r"\binsufficient\b|not\s+sufficient|证据不足|无法回答", text, flags=re.IGNORECASE):
        sufficient_default = False
    elif re.search(r"\bsufficient\b|evidence\s+is\s+enough|证据充分", text, flags=re.IGNORECASE):
        sufficient_default = True

    sufficient = parse_bool_key("sufficient", sufficient_default)
    should_update = parse_bool_key("should_update", not sufficient)
    answerability_confidence = parse_float_key("answerability_confidence", 0.2 if sufficient else 0.0)
    missing_information = parse_string_key("missing_information")
    if not missing_information:
        missing_information = (
            "Unstructured reflection output; missing-information detail could not be parsed reliably."
        )
    next_search_strategy = parse_string_key("next_search_strategy")
    if not next_search_strategy:
        next_search_strategy = "shift" if should_update else "stop"
    reasoning = truncate_text(compact or "Reflection output was unstructured; deterministic fallback applied.", 500)

    return {
        "sufficient": bool(sufficient),
        "should_update": bool(should_update),
        "answerability_confidence": float(answerability_confidence),
        "missing_information": missing_information,
        "next_search_strategy": next_search_strategy,
        "reasoning": reasoning,
    }


def fallback_qa_from_unstructured_raw(raw: str) -> Dict[str, Any]:
    text = (raw or "").strip()
    compact = re.sub(r"\s+", " ", re.sub(r"</?think>", " ", text, flags=re.IGNORECASE)).strip()
    answer = ""

    answer_patterns = [
        r"(?im)^\s*(?:answer|final\s*answer|结论|最终答案)\s*[:：]\s*(.+)$",
        r"(?im)^\s*\"answer\"\s*[:：]\s*\"([^\"]+)\"",
    ]
    for pattern in answer_patterns:
        match = re.search(pattern, text)
        if match:
            answer = match.group(1).strip().strip('"').strip("'")
            break

    uncertain = bool(
        re.search(
            r"无法确定|不能确定|cannot\s+determine|insufficient\s+evidence|not\s+enough\s+evidence",
            compact,
            flags=re.IGNORECASE,
        )
    )
    if not answer:
        answer = "无法确定" if uncertain else truncate_text(compact, 160)

    confidence = 0.1 if uncertain else 0.4
    conf_match = re.search(r"[\"']?confidence[\"']?\s*[:=]\s*([-+]?\d*\.?\d+)", text, flags=re.IGNORECASE)
    if conf_match:
        try:
            confidence = max(0.0, min(1.0, float(conf_match.group(1))))
        except ValueError:
            pass

    reasoning = truncate_text(
        compact or "Synthesizer output was unstructured; deterministic fallback applied.",
        500,
    )
    return {
        "answer": answer,
        "confidence": float(confidence),
        "reasoning": reasoning,
    }


def call_role_with_retries(
    runner: QwenChatRunner,
    role: str,
    prompt: str,
    schema: Dict[str, Any],
    run_dir: Path,
    images: Optional[Sequence[Image.Image]] = None,
    max_new_tokens: int = 1024,
    retries: int = 2,
) -> Tuple[str, Dict[str, Any], int]:
    strict_json_guard = (
        "Output format rules (mandatory):\n"
        "- Reply with exactly one JSON object and nothing else.\n"
        "- Do not output <think>...</think>, markdown, code fences, or prose outside JSON.\n"
        "- The first character of your reply must be '{' and the last character must be '}'.\n"
        "- Keep values concise while preserving required schema fields."
    )
    base_prompt = f"{prompt}\n\n{strict_json_guard}"
    write_text(run_dir / f"{role}.prompt.txt", base_prompt)
    current_prompt = base_prompt
    last_raw = ""
    for attempt in range(1, retries + 2):
        raw = runner.generate(current_prompt, images=images, max_new_tokens=max_new_tokens)
        last_raw = raw
        write_text(run_dir / f"{role}.raw_response.attempt{attempt}.txt", raw)
        data = parse_json_response(raw)
        if data is not None and validate_against_schema(data, schema):
            write_json(run_dir / f"{role}.parsed.json", data)
            return raw, data, attempt - 1
        current_prompt = (
            f"{base_prompt}\n\n"
            f"Retry instruction for {role}:\n"
            f"- Your previous reply was invalid because it was not a complete JSON object matching the schema.\n"
            f"- Reply again with exactly one JSON object.\n"
            f"- Do not include Thinking Process, markdown, code fences, bullet points, or any text before or after the JSON.\n"
            f"- The first character of your reply must be '{{' and the last character must be '}}'."
        )
        write_text(run_dir / f"{role}.repair_prompt.attempt{attempt}.txt", current_prompt)
    if schema == EVIDENCE_SCHEMA:
        fallback_data = fallback_evidence_from_unstructured_raw(last_raw)
        write_json(run_dir / f"{role}.parsed.json", fallback_data)
        write_json(
            run_dir / f"{role}.schema_recovery.json",
            {
                "role": role,
                "recovery_mode": "deterministic_unstructured_fallback",
                "retries_exhausted": retries + 1,
                "note": "Observer output stayed unstructured; converted raw text into schema fields with empty key_evidence.",
            },
        )
        return last_raw, fallback_data, retries + 1
    if schema == REFLECTION_SCHEMA:
        fallback_data = fallback_reflection_from_unstructured_raw(last_raw)
        write_json(run_dir / f"{role}.parsed.json", fallback_data)
        write_json(
            run_dir / f"{role}.schema_recovery.json",
            {
                "role": role,
                "recovery_mode": "deterministic_unstructured_fallback",
                "retries_exhausted": retries + 1,
                "note": "Reflector output stayed unstructured; converted raw text into reflection schema fields.",
            },
        )
        return last_raw, fallback_data, retries + 1
    if schema == QA_SCHEMA:
        fallback_data = fallback_qa_from_unstructured_raw(last_raw)
        write_json(run_dir / f"{role}.parsed.json", fallback_data)
        write_json(
            run_dir / f"{role}.schema_recovery.json",
            {
                "role": role,
                "recovery_mode": "deterministic_unstructured_fallback",
                "retries_exhausted": retries + 1,
                "note": "Synthesizer output stayed unstructured; converted raw text into QA schema fields.",
            },
        )
        return last_raw, fallback_data, retries + 1
    raise RuntimeError(f"{role} failed schema validation after retries. Last raw output:\n{last_raw}")


def build_reflector_output(evidence: Dict[str, Any]) -> Dict[str, Any]:
    items = evidence.get("key_evidence", []) or []
    detailed = (evidence.get("detailed_response") or "").strip()
    sufficient = bool(items) and bool(detailed)
    query_confidence = 0.7 if sufficient else 0.2
    return {
        "sufficient": sufficient,
        "should_update": not sufficient,
        "updates": [],
        "reasoning": (
            f"Evidence items={len(items)}, detailed_response_present={bool(detailed)}. "
            f"Query confidence={query_confidence:.2f}"
        ),
        "confidence": 0.8 if sufficient else 0.5,
        "query_confidence": query_confidence,
        "event": "VERIFICATION",
    }


def exact_or_rule_match(predicted: str, reference: str) -> Optional[Dict[str, Any]]:
    norm_pred = normalize_text(predicted)
    norm_ref = normalize_text(reference)
    if norm_pred and norm_pred == norm_ref:
        return {
            "correct": True,
            "score_method": "normalized_exact",
            "judge_reason": "Normalized predicted answer exactly matches normalized reference answer.",
        }
    if norm_pred and norm_ref and (norm_pred in norm_ref or norm_ref in norm_pred):
        return {
            "correct": True,
            "score_method": "normalized_substring",
            "judge_reason": "Normalized predicted and reference answers have a direct containment match.",
        }
    return None


def _parse_bool_like(value: Any) -> Optional[bool]:
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    text = str(value).strip().lower()
    if text in {"true", "t", "yes", "y", "1", "correct", "right"}:
        return True
    if text in {"false", "f", "no", "n", "0", "incorrect", "wrong"}:
        return False
    return None


def concise_judge_reason(raw: str) -> str:
    text = (raw or "").strip()
    if not text:
        return ""
    for pattern in (r"(?im)^reason\s*:\s*(.+)$", r"(?im)^原因[:：]\s*(.+)$"):
        match = re.search(pattern, text)
        if match:
            return truncate_text(match.group(1).strip(), 320)
    return truncate_text(text, 320)


def parse_judger_verdict_line(raw: str) -> Optional[Tuple[bool, str]]:
    text = (raw or "").strip()
    if not text:
        return None

    explicit_patterns = [
        r"(?im)^\s*verdict\s*[:：]\s*(correct|incorrect)\b(?:\s*[-:]\s*(.*))?$",
        r"(?im)^\s*result\s*[:：]\s*(correct|incorrect)\b(?:\s*[-:]\s*(.*))?$",
        r"(?im)^\s*(correct|incorrect)\s*$",
    ]
    for pattern in explicit_patterns:
        match = re.search(pattern, text)
        if not match:
            continue
        label = match.group(1).strip().lower()
        verdict = label == "correct"
        inline_reason = ""
        if match.lastindex and match.lastindex >= 2 and match.group(2):
            inline_reason = match.group(2).strip()
        if not inline_reason:
            inline_reason = concise_judge_reason(text)
        return verdict, truncate_text(inline_reason, 320)

    zh_patterns = [
        r"(?im)^\s*结论\s*[:：]\s*(正确|错误|不正确)\b",
        r"(?im)^\s*(正确|错误|不正确)\s*$",
    ]
    for pattern in zh_patterns:
        match = re.search(pattern, text)
        if not match:
            continue
        label = match.group(1)
        verdict = label == "正确"
        return verdict, concise_judge_reason(text)

    return None


def parse_judger_output(raw: str) -> Optional[Dict[str, Any]]:
    parsed = parse_json_response(raw)
    if isinstance(parsed, dict):
        for key in ("correct", "is_correct", "verdict", "label", "result"):
            verdict = _parse_bool_like(parsed.get(key))
            if verdict is not None:
                reason = (
                    parsed.get("judge_reason")
                    or parsed.get("reason")
                    or parsed.get("analysis")
                    or parsed.get("explanation")
                    or concise_judge_reason(raw)
                )
                score_method = parsed.get("score_method") or "semantic_judger"
                return {
                    "correct": verdict,
                    "score_method": str(score_method),
                    "judge_reason": truncate_text(str(reason).strip(), 320),
                }
        raw = json.dumps(parsed, ensure_ascii=False)

    verdict_line = parse_judger_verdict_line(raw)
    if verdict_line is not None:
        verdict, reason = verdict_line
        return {
            "correct": verdict,
            "score_method": "semantic_judger",
            "judge_reason": reason,
        }

    tail = (raw or "").strip()[-400:].lower()
    if tail:
        incorrect_tail_patterns = [
            r"\bincorrect\b",
            r"\bnot\s+correct\b",
            r"\bwrong\b",
            r"不正确",
            r"错误",
        ]
        for pattern in incorrect_tail_patterns:
            if re.search(pattern, tail):
                return {
                    "correct": False,
                    "score_method": "semantic_judger_tail_inference",
                    "judge_reason": concise_judge_reason(raw),
                }
        correct_tail_patterns = [
            r"\bcorrect\b",
            r"\bright\b",
            r"正确",
        ]
        for pattern in correct_tail_patterns:
            if re.search(pattern, tail):
                return {
                    "correct": True,
                    "score_method": "semantic_judger_tail_inference",
                    "judge_reason": concise_judge_reason(raw),
                }

    return None


def call_judger_with_tolerant_parsing(
    runner: QwenChatRunner,
    prompt: str,
    run_dir: Path,
    max_new_tokens: int = 256,
    retries: int = 2,
) -> Tuple[str, Dict[str, Any], int]:
    base_prompt = prompt
    write_text(run_dir / "judge.prompt.txt", prompt)
    last_raw = ""
    for attempt in range(1, retries + 2):
        raw = runner.generate(prompt, images=None, max_new_tokens=max_new_tokens)
        last_raw = raw
        write_text(run_dir / f"judge.raw_response.attempt{attempt}.txt", raw)
        parsed = parse_judger_output(raw)
        if parsed is not None:
            write_json(run_dir / "judge.parsed.json", parsed)
            return raw, parsed, attempt - 1
        prompt = (
            f"{base_prompt}\n\n"
            "Retry instruction for judge:\n"
            "- Your previous response did not clearly state whether the prediction is Correct or Incorrect.\n"
            "- Reply with a short verdict line using exactly one of: 'Verdict: Correct' or 'Verdict: Incorrect'.\n"
            "- Then provide one short reason line.\n"
            "- Do not output thinking process."
        )
        write_text(run_dir / f"judge.repair_prompt.attempt{attempt}.txt", prompt)
    fallback = {
        "correct": False,
        "score_method": "semantic_judger_no_verdict",
        "judge_reason": "Judge did not provide an explicit verdict after retries; marked incorrect by fail-closed policy.",
    }
    write_json(run_dir / "judge.parsed.json", fallback)
    write_json(
        run_dir / "judge.schema_recovery.json",
        {
            "role": "judge",
            "recovery_mode": "fail_closed_no_verdict",
            "retries_exhausted": retries + 1,
            "note": "Judge output remained non-verdict text; conservative incorrect label applied.",
        },
    )
    return last_raw, fallback, retries + 1


def save_frame_preview(images: Sequence[Image.Image], metadata: Sequence[Dict[str, Any]], frames_dir: Path) -> None:
    frames_dir.mkdir(parents=True, exist_ok=True)
    for item, image in zip(metadata, images):
        ts = str(item["timestamp_sec"]).replace(".", "_")
        image.save(frames_dir / f"frame_{item['index']:02d}_{ts}.jpg", format="JPEG", quality=90)


def main(argv: Optional[Sequence[str]] = None) -> Dict[str, Any]:
    parser = argparse.ArgumentParser(description="Run the first SportsTime QA sample with local Qwen3.5-9B.")
    parser.add_argument(
        "--question-file",
        default="dataset/SportsTime/basketball_full_question_first200.json",
        help="Path to the SportsTime QA file.",
    )
    parser.add_argument(
        "--dataset-root",
        default="dataset/Basketball/Full",
        help="Root directory containing basketball video folders.",
    )
    parser.add_argument(
        "--question-index",
        type=int,
        default=0,
        help="Question index within the QA file.",
    )
    parser.add_argument(
        "--model-path",
        default="/home/guoxiangyu/.cache/modelscope/hub/models/Qwen/Qwen3.5-9B",
        help="Local Qwen3.5-9B model path.",
    )
    parser.add_argument(
        "--out",
        default="avp/out/qwen_first_question_demo",
        help="Base output directory for traces.",
    )
    parser.add_argument(
        "--max-frames",
        type=int,
        default=0,
        help="Maximum sampled frames per observer span. Use 0 to disable capping (keeps requested FPS; slower).",
    )
    parser.add_argument(
        "--image-max-side",
        type=int,
        default=0,
        help="Resize sampled frames so the longest side is at most this many pixels. Use 0 to keep the original size.",
    )
    parser.add_argument(
        "--observer-chunk-size",
        type=int,
        default=0,
        help="Legacy fallback: fixed frame count per observer call. Only used when --observer-window-sec <= 0.",
    )
    parser.add_argument(
        "--observer-window-sec",
        type=float,
        default=30.0,
        help="Continuous time span in seconds per observer call. Planner-sampled frames are grouped by temporal window instead of by fixed frame count.",
    )
    parser.add_argument(
        "--observer-window-max-frames",
        type=int,
        default=16,
        help="Maximum number of frames to keep per observer time window after temporal grouping. Use 0 to disable the cap.",
    )
    parser.add_argument(
        "--max-rounds",
        type=int,
        default=30,
        help="Maximum number of plan-observe-reflect-replan rounds before stopping.",
    )
    parser.add_argument(
        "--uniform-window-sec",
        type=float,
        default=180.0,
        help="Window size in seconds for generic uniform relocation scans.",
    )
    parser.add_argument(
        "--answer-confidence-threshold",
        type=float,
        default=0.75,
        help="Minimum provisional answer confidence required before the controller stops early.",
    )
    args = parser.parse_args(argv)

    question = load_question_by_index(Path(args.question_file), Path(args.dataset_root), args.question_index)
    run_dir = make_run_dir(Path(args.out), question.id)
    write_json(run_dir / "question.json", question.__dict__)

    runner = get_cached_runner(args.model_path)

    round_records: List[Dict[str, Any]] = []
    watched_intervals: List[Tuple[float, float]] = []
    final_record: Optional[Dict[str, Any]] = None
    stop_reason = "round_budget_exhausted"

    for round_idx in range(1, args.max_rounds + 1):
        planner_role = f"planner.round{round_idx:02d}"
        if round_records:
            planning_context = build_replanning_evidence_summary(round_records, watched_intervals, question.duration)
            planner_prompt = PromptManager.get_replanning_prompt(
                question.question,
                {"duration_sec": question.duration},
                planning_context,
                options=None,
            )
        else:
            planning_context = ""
            planner_prompt = PromptManager.get_planning_prompt(
                question.question,
                {"duration_sec": question.duration},
                options=None,
            )

        planner_raw, plan_data, planner_retries = call_role_with_retries(
            runner,
            role=planner_role,
            prompt=planner_prompt,
            schema=PLAN_SCHEMA,
            run_dir=run_dir,
            images=None,
            max_new_tokens=1024,
        )

        step = plan_data["steps"][0]
        regions = step.get("regions", []) or []
        start_sec, end_sec, sampling_strategy = resolve_observation_span(
            question=question.question,
            duration=question.duration,
            load_mode=step.get("load_mode", "uniform"),
            regions=regions,
            observed_intervals=watched_intervals,
            uniform_window_sec=args.uniform_window_sec,
        )

        sampled_images, sampled_meta = sample_frames(
            Path(question.path),
            start_sec=start_sec,
            end_sec=end_sec,
            fps=float(step.get("fps", 0.5)),
            max_frames=args.max_frames,
            image_max_side=args.image_max_side,
        )
        if not sampled_images:
            raise RuntimeError(
                f"No frames were sampled for round {round_idx} over span {start_sec:.1f}-{end_sec:.1f}s."
            )

        save_frame_preview(sampled_images, sampled_meta, run_dir / f"frames_round{round_idx:02d}")
        write_json(
            run_dir / f"observer.round{round_idx:02d}.frame_metadata.json",
            {
                "round_idx": round_idx,
                "sampling_strategy": sampling_strategy,
                "start_sec": start_sec,
                "end_sec": end_sec,
                "frame_count": len(sampled_meta),
                "max_frames": args.max_frames,
                "image_max_side": args.image_max_side,
                "observer_chunk_size": args.observer_chunk_size,
                "observer_window_sec": args.observer_window_sec,
                "observer_window_max_frames": args.observer_window_max_frames,
                "watched_intervals_before_round": merge_intervals(watched_intervals, question.duration),
                "coverage_ratio_before_round": coverage_ratio(watched_intervals, question.duration),
                "frames": sampled_meta,
            },
        )

        observer_chunks = build_observer_chunks(
            images=sampled_images,
            metadata=sampled_meta,
            segment_start_sec=start_sec,
            segment_end_sec=end_sec,
            window_sec=args.observer_window_sec,
            legacy_chunk_size=args.observer_chunk_size,
            max_frames_per_chunk=args.observer_window_max_frames,
        )
        if not observer_chunks:
            raise RuntimeError("Observer chunking produced no temporal chunks.")

        observer_chunk_records: List[Dict[str, Any]] = []
        observer_raw_blocks: List[str] = []
        observer_retries = 0
        observer_context = build_observer_context(round_records)

        for chunk in observer_chunks:
            chunk_index = int(chunk["chunk_id"])
            chunk_images = chunk["images"]
            chunk_meta = chunk["metadata"]
            chunk_start_sec = float(chunk["start_sec"])
            chunk_end_sec = float(chunk["end_sec"])
            chunk_role = f"observer.round{round_idx:02d}.chunk{chunk_index:02d}"
            chunk_context = (
                f"{observer_context}\n\n"
                f"Current high-resolution observer chunk {chunk_index}/{len(observer_chunks)} for round {round_idx}. "
                f"Focus only on what is visible in this chunk. If the target event is not visible here, say so clearly."
            )
            chunk_prompt = build_observer_chunk_prompt(
                question=question.question,
                sub_query=step.get("sub_query", question.question),
                context=chunk_context,
                start_sec=chunk_start_sec,
                end_sec=chunk_end_sec,
                video_duration=question.duration,
            )
            chunk_raw, chunk_data, chunk_retries = call_role_with_retries(
                runner,
                role=chunk_role,
                prompt=chunk_prompt,
                schema=EVIDENCE_SCHEMA,
                run_dir=run_dir,
                images=chunk_images,
                max_new_tokens=1024,
            )
            observer_retries += chunk_retries
            write_json(
                run_dir / f"{chunk_role}.frame_metadata.json",
                {
                    "round_idx": round_idx,
                    "chunk_index": chunk_index,
                    "chunk_count": len(observer_chunks),
                    "start_sec": chunk_start_sec,
                    "end_sec": chunk_end_sec,
                    "chunk_mode": chunk["mode"],
                    "observer_window_sec": args.observer_window_sec,
                    "observer_window_max_frames": args.observer_window_max_frames,
                    "source_frame_count": int(chunk["source_frame_count"]),
                    "frame_count": len(chunk_meta),
                    "frames": chunk_meta,
                },
            )
            observer_chunk_records.append(
                {
                    "chunk_id": chunk_index,
                    "start_sec": chunk_start_sec,
                    "end_sec": chunk_end_sec,
                    "chunk_mode": chunk["mode"],
                    "source_frame_count": int(chunk["source_frame_count"]),
                    "frame_count": len(chunk_meta),
                    "retry_count": chunk_retries,
                    "role": chunk_role,
                    "parsed": chunk_data,
                    "raw_trace": f"{chunk_role}.raw_response.attempt1.txt",
                    "parsed_trace": f"{chunk_role}.parsed.json",
                }
            )
            observer_raw_blocks.append(
                f"=== {chunk_role} | {chunk_start_sec:.1f}-{chunk_end_sec:.1f}s | frames={len(chunk_meta)} ===\n{chunk_raw.strip()}"
            )

        evidence_data = aggregate_evidence_chunks(observer_chunk_records)
        round_observer_raw = "\n\n".join(observer_raw_blocks)
        write_json(
            run_dir / f"observer.round{round_idx:02d}.chunk_manifest.json",
            {
                "round_idx": round_idx,
                "chunk_count": len(observer_chunk_records),
                "observer_chunk_size": args.observer_chunk_size,
                "observer_window_sec": args.observer_window_sec,
                "observer_window_max_frames": args.observer_window_max_frames,
                "chunks": [
                    {
                        "chunk_id": record["chunk_id"],
                        "start_sec": record["start_sec"],
                        "end_sec": record["end_sec"],
                        "chunk_mode": record["chunk_mode"],
                        "source_frame_count": record["source_frame_count"],
                        "frame_count": record["frame_count"],
                        "retry_count": record["retry_count"],
                        "role": record["role"],
                        "raw_trace": record["raw_trace"],
                        "parsed_trace": record["parsed_trace"],
                    }
                    for record in observer_chunk_records
                ],
            },
        )
        write_text(run_dir / f"observer.round{round_idx:02d}.raw_response.attempt1.txt", round_observer_raw)
        write_json(run_dir / f"observer.round{round_idx:02d}.parsed.json", evidence_data)

        watched_intervals = merge_intervals(watched_intervals + [(start_sec, end_sec)], question.duration)

        provisional_round_records = list(round_records) + [
            {
                "round_idx": round_idx,
                "start_sec": start_sec,
                "end_sec": end_sec,
                "sampling_strategy": sampling_strategy,
                "plan_data": plan_data,
                "plan_summary": plan_to_summary(plan_data),
                "evidence_data": evidence_data,
            }
        ]
        evidence_summary = build_all_evidence_summary(provisional_round_records)
        synthesis_role = f"synthesizer.round{round_idx:02d}"
        synthesis_prompt = build_open_qa_synthesis_prompt(
            question=question.question,
            all_evidence=evidence_summary,
            video_duration=question.duration,
        )
        synthesizer_raw, synthesis_data, synthesizer_retries = call_role_with_retries(
            runner,
            role=synthesis_role,
            prompt=synthesis_prompt,
            schema=QA_SCHEMA,
            run_dir=run_dir,
            images=None,
            max_new_tokens=1024,
        )

        reflection_role = f"reflector.round{round_idx:02d}"
        reflection_context = build_replanning_evidence_summary(
            provisional_round_records,
            watched_intervals,
            question.duration,
        )
        reflection_prompt = PromptManager.get_answerability_reflection_prompt(
            query=question.question,
            video_meta={"duration_sec": question.duration},
            evidence_summary=reflection_context,
            watched_intervals_text=(
                f"{intervals_to_text(watched_intervals)}\n"
                f"Coverage ratio: {coverage_ratio(watched_intervals, question.duration):.2f}"
            ),
            provisional_answer=synthesis_data["answer"],
            provisional_confidence=float(synthesis_data.get("confidence", 0.0)),
            round_index=round_idx,
            max_rounds=args.max_rounds,
        )
        reflection_raw, reflection_data, reflection_retries = call_role_with_retries(
            runner,
            role=reflection_role,
            prompt=reflection_prompt,
            schema=REFLECTION_SCHEMA,
            run_dir=run_dir,
            images=None,
            max_new_tokens=512,
        )

        round_record = {
            "round_idx": round_idx,
            "planner_role": planner_role,
            "planner_prompt": planner_prompt,
            "planner_raw": planner_raw,
            "planner_retries": planner_retries,
            "plan_data": plan_data,
            "plan_summary": plan_to_summary(plan_data),
            "start_sec": start_sec,
            "end_sec": end_sec,
            "sampling_strategy": sampling_strategy,
            "observer_retries": observer_retries,
            "observer_raw": round_observer_raw,
            "evidence_data": evidence_data,
            "synthesis_role": synthesis_role,
            "synthesis_prompt": synthesis_prompt,
            "synthesizer_raw": synthesizer_raw,
            "synthesizer_retries": synthesizer_retries,
            "synthesis_data": synthesis_data,
            "reflection_role": reflection_role,
            "reflection_prompt": reflection_prompt,
            "reflection_raw": reflection_raw,
            "reflection_retries": reflection_retries,
            "reflection_data": reflection_data,
            "coverage_ratio": coverage_ratio(watched_intervals, question.duration),
        }
        round_records.append(round_record)

        provisional_confidence = float(synthesis_data.get("confidence", 0.0))
        if reflection_data.get("sufficient", False) and provisional_confidence >= args.answer_confidence_threshold:
            stop_reason = "high_confidence_answer"
            final_record = round_record
            break
        if coverage_ratio(watched_intervals, question.duration) >= 0.995:
            stop_reason = "video_coverage_exhausted"
            final_record = round_record
            break
        if round_idx == args.max_rounds:
            stop_reason = "round_budget_exhausted"
            final_record = round_record
            break

    if final_record is None:
        raise RuntimeError("Controller completed without producing a final round record.")

    combined_evidence = aggregate_evidence_chunks(
        [
            {
                "chunk_id": record["round_idx"],
                "start_sec": record["start_sec"],
                "end_sec": record["end_sec"],
                "parsed": record["evidence_data"],
            }
            for record in round_records
        ]
    )
    write_text(
        run_dir / "planner.prompt.txt",
        final_record["planner_prompt"],
    )
    write_text(
        run_dir / "planner.raw_response.attempt1.txt",
        final_record["planner_raw"],
    )
    write_json(run_dir / "planner.parsed.json", final_record["plan_data"])
    write_text(
        run_dir / "observer.prompt.txt",
        build_replanning_evidence_summary(round_records, watched_intervals, question.duration),
    )
    write_text(
        run_dir / "observer.raw_response.attempt1.txt",
        "\n\n".join(record["observer_raw"] for record in round_records),
    )
    write_json(
        run_dir / "observer.frame_metadata.json",
        {
            "rounds_executed": len(round_records),
            "coverage_ratio": coverage_ratio(watched_intervals, question.duration),
            "watched_intervals": watched_intervals,
            "observer_window_sec": args.observer_window_sec,
            "observer_window_max_frames": args.observer_window_max_frames,
            "max_rounds": args.max_rounds,
            "rounds": [
                {
                    "round_idx": record["round_idx"],
                    "start_sec": record["start_sec"],
                    "end_sec": record["end_sec"],
                    "sampling_strategy": record["sampling_strategy"],
                    "frame_metadata_file": f"observer.round{record['round_idx']:02d}.frame_metadata.json",
                }
                for record in round_records
            ],
        },
    )
    write_json(
        run_dir / "observer.chunk_manifest.json",
        {
            "rounds_executed": len(round_records),
            "rounds": [
                {
                    "round_idx": record["round_idx"],
                    "chunk_manifest_file": f"observer.round{record['round_idx']:02d}.chunk_manifest.json",
                }
                for record in round_records
            ],
        },
    )
    write_json(run_dir / "observer.parsed.json", combined_evidence)
    write_text(run_dir / "reflector.prompt.txt", final_record["reflection_prompt"])
    write_text(run_dir / "reflector.raw_response.attempt1.txt", final_record["reflection_raw"])
    write_json(
        run_dir / "reflector.input.json",
        {
            "rounds": len(round_records),
            "watched_intervals": watched_intervals,
            "coverage_ratio": coverage_ratio(watched_intervals, question.duration),
            "provisional_answer": final_record["synthesis_data"]["answer"],
            "provisional_confidence": final_record["synthesis_data"]["confidence"],
        },
    )
    write_json(run_dir / "reflector.output.json", final_record["reflection_data"])
    write_text(run_dir / "synthesizer.prompt.txt", final_record["synthesis_prompt"])
    write_text(run_dir / "synthesizer.raw_response.attempt1.txt", final_record["synthesizer_raw"])
    write_json(run_dir / "synthesizer.parsed.json", final_record["synthesis_data"])
    write_json(
        run_dir / "controller.rounds.json",
        {
            "max_rounds": args.max_rounds,
            "rounds_executed": len(round_records),
            "coverage_ratio": coverage_ratio(watched_intervals, question.duration),
            "watched_intervals": watched_intervals,
            "stop_reason": stop_reason,
            "rounds": [
                {
                    "round_idx": record["round_idx"],
                    "plan_summary": record["plan_summary"],
                    "start_sec": record["start_sec"],
                    "end_sec": record["end_sec"],
                    "sampling_strategy": record["sampling_strategy"],
                    "planner_retries": record["planner_retries"],
                    "observer_retries": record["observer_retries"],
                    "synthesizer_retries": record["synthesizer_retries"],
                    "reflection_retries": record["reflection_retries"],
                    "provisional_answer": record["synthesis_data"]["answer"],
                    "provisional_confidence": record["synthesis_data"]["confidence"],
                    "reflection": record["reflection_data"],
                    "coverage_ratio": record["coverage_ratio"],
                }
                for record in round_records
            ],
        },
    )

    plan_data = final_record["plan_data"]
    synthesis_data = final_record["synthesis_data"]
    evidence_data = combined_evidence

    predicted_answer = synthesis_data["answer"]
    rule_result = exact_or_rule_match(predicted_answer, question.answer)
    if rule_result is not None:
        judge_data = rule_result
        write_text(run_dir / "judge.prompt.txt", "Rule-based exact/substr match applied before semantic judging.")
        write_text(run_dir / "judge.raw_response.attempt1.txt", json.dumps(rule_result, ensure_ascii=False, indent=2))
        write_json(run_dir / "judge.parsed.json", judge_data)
        judge_retries = 0
    else:
        judge_prompt = build_correctness_judge_prompt(
            question=question.question,
            predicted_answer=predicted_answer,
            reference_answer=question.answer,
            cot_reference=question.cot_reference,
        )
        _, judge_data, judge_retries = call_judger_with_tolerant_parsing(
            runner=runner,
            prompt=judge_prompt,
            run_dir=run_dir,
            max_new_tokens=768,
        )

    recovery_files = sorted(run_dir.glob("*.schema_recovery.json"))
    recovery_names = [path.name for path in recovery_files]

    def has_recovery(prefix: str) -> bool:
        return any(name.startswith(prefix) for name in recovery_names)

    history: List[Dict[str, Any]] = []
    for record in round_records:
        round_idx = record["round_idx"]
        history.extend(
            [
                {
                    "role": "planner",
                    "round": round_idx,
                    "retry_count": record["planner_retries"],
                    "trace_file": f"planner.round{round_idx:02d}.raw_response.attempt1.txt",
                    "parsed_file": f"planner.round{round_idx:02d}.parsed.json",
                    "schema_valid": not has_recovery(f"planner.round{round_idx:02d}."),
                },
                {
                    "role": "observer",
                    "round": round_idx,
                    "retry_count": record["observer_retries"],
                    "trace_file": f"observer.round{round_idx:02d}.raw_response.attempt1.txt",
                    "parsed_file": f"observer.round{round_idx:02d}.parsed.json",
                    "schema_valid": not has_recovery(f"observer.round{round_idx:02d}."),
                },
                {
                    "role": "synthesizer",
                    "round": round_idx,
                    "retry_count": record["synthesizer_retries"],
                    "trace_file": f"synthesizer.round{round_idx:02d}.raw_response.attempt1.txt",
                    "parsed_file": f"synthesizer.round{round_idx:02d}.parsed.json",
                    "schema_valid": not has_recovery(f"synthesizer.round{round_idx:02d}."),
                },
                {
                    "role": "reflector",
                    "round": round_idx,
                    "retry_count": record["reflection_retries"],
                    "trace_file": f"reflector.round{round_idx:02d}.raw_response.attempt1.txt",
                    "parsed_file": f"reflector.round{round_idx:02d}.parsed.json",
                    "schema_valid": not has_recovery(f"reflector.round{round_idx:02d}."),
                },
            ]
        )
    history.append(
        {
            "role": "judge",
            "round": len(round_records),
            "retry_count": judge_retries,
            "trace_file": "judge.raw_response.attempt1.txt",
            "parsed_file": "judge.parsed.json",
            "schema_valid": not has_recovery("judge."),
        }
    )
    write_json(run_dir / "history.jsonl.json", history)

    summary = {
        "question_id": question.id,
        "video_id": question.video_id,
        "question": question.question,
        "reference_answer": question.answer,
        "predicted_answer": predicted_answer,
        "correct": judge_data["correct"],
        "score_method": judge_data["score_method"],
        "judge_reason": judge_data["judge_reason"],
        "sampling_strategy": final_record["sampling_strategy"],
        "plan_summary": plan_to_summary(plan_data),
        "rounds_executed": len(round_records),
        "coverage_ratio": coverage_ratio(watched_intervals, question.duration),
        "watched_intervals": watched_intervals,
        "observer_window_sec": args.observer_window_sec,
        "observer_window_max_frames": args.observer_window_max_frames,
        "max_rounds": args.max_rounds,
        "stop_reason": stop_reason,
        "answer_confidence": synthesis_data.get("confidence"),
        "answerability_confidence": final_record["reflection_data"].get("answerability_confidence"),
        "run_dir": str(run_dir.resolve()),
        "role_contract_status": "passed" if not recovery_names else "recovered_with_fallback",
        "schema_recoveries": recovery_names,
        "trace_available": True,
    }
    write_json(run_dir / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return summary


if __name__ == "__main__":
    main()
