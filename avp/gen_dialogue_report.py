"""
avp/gen_dialogue_report.py
Generate a human-readable Markdown dialogue report from a single AVP run directory.

Usage:
    python -m avp.gen_dialogue_report --run-dir <path> [--out <report.md>]

The run directory should contain:
    role_traces.jsonl        -- per-role LLM prompt/response records
    plan.initial.json        -- initial planner output
    evidence/round_*/        -- per-round evidence JSON files
    final_answer.json        -- synthesized final answer
    conversation_history.json (optional)
    sample_metadata.json     (optional)
"""

import argparse
import json
import pathlib
import textwrap
from typing import Any, Dict, List, Optional, Tuple


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _load_jsonl(path: pathlib.Path) -> List[Dict[str, Any]]:
    records = []
    if path.exists():
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
    return records


def _load_json(path: pathlib.Path) -> Optional[Dict[str, Any]]:
    if path.exists():
        with open(path, encoding="utf-8") as fh:
            try:
                return json.load(fh)
            except json.JSONDecodeError:
                return None
    return None


def _wrap(text: str, width: int = 100, indent: str = "") -> str:
    """Wrap long text for readability in Markdown code blocks."""
    lines = []
    for paragraph in text.split("\n"):
        if len(paragraph) <= width:
            lines.append(indent + paragraph)
        else:
            lines.extend(
                indent + line
                for line in textwrap.wrap(paragraph, width=width,
                                          break_long_words=False,
                                          break_on_hyphens=False)
            )
    return "\n".join(lines)


def _fmt_seconds(sec: float) -> str:
    m, s = divmod(int(sec), 60)
    return f"{m:02d}:{s:02d}"


def _role_icon(role: str) -> str:
    return {
        "planner":              "🗺️  **Planner**",
        "planner_replan":       "🔄  **Planner (replan)**",
        "observer":             "👁️  **Observer**",
        "reflector":            "🔍  **Reflector** (heuristic)",
        "reflector_synthesizer":"🔍  **Reflector** (LLM)",
        "synthesizer":          "🎯  **Synthesizer**",
    }.get(role, f"❓  **{role}**")


def _fmt_seconds_list(values: List[Any]) -> str:
    secs: List[float] = []
    for value in values or []:
        if isinstance(value, (int, float)):
            secs.append(float(value))
    if not secs:
        return "—"
    return ", ".join(f"`{_fmt_seconds(sec)}`" for sec in secs)


def _load_evidence_payloads(run_dir: pathlib.Path) -> List[Dict[str, Any]]:
    ev_dir = run_dir / "evidence"
    payloads: List[Dict[str, Any]] = []
    if not ev_dir.exists():
        return payloads
    for ev_file in sorted(ev_dir.glob("round_*/evidence.json")):
        ev = _load_json(ev_file)
        if ev:
            payloads.append(ev)
    return payloads


def _fmt_interval(start: Any, end: Any) -> str:
    if isinstance(start, (int, float)) and isinstance(end, (int, float)):
        return f"`{_fmt_seconds(float(start))}` – `{_fmt_seconds(float(end))}`"
    if isinstance(start, (int, float)):
        return f"`{_fmt_seconds(float(start))}`"
    return "—"


def _extract_time_contract(
    meta: Optional[Dict[str, Any]],
    sample_meta: Optional[Dict[str, Any]],
    evidence_payloads: List[Dict[str, Any]],
) -> Tuple[str, str]:
    annotation_time_base = str(
        (sample_meta or {}).get("time_base")
        or (meta or {}).get("time_base")
        or "raw_video_seconds"
    )
    canonical_time_base = "raw_video_seconds"
    for ev in evidence_payloads:
        normalization = (((ev or {}).get("model_call") or {}).get("time_normalization") or {})
        candidate = normalization.get("canonical_reference")
        if candidate:
            canonical_time_base = str(candidate)
            break
    return annotation_time_base, canonical_time_base


# ──────────────────────────────────────────────────────────────────────────────
# Section builders
# ──────────────────────────────────────────────────────────────────────────────

def _section_overview(
    meta: Optional[Dict],
    sample_meta: Optional[Dict],
    final: Optional[Dict],
    evidence_payloads: List[Dict[str, Any]],
) -> str:
    lines = ["## 概览 Overview\n"]
    annotation_time_base, canonical_time_base = _extract_time_contract(meta, sample_meta, evidence_payloads)

    if sample_meta:
        lines.append(f"- **视频 ID**: `{sample_meta.get('video_id', 'N/A')}`")
        lines.append(f"- **问题**: {sample_meta.get('question', 'N/A')}")
        ref = sample_meta.get("reference_answer") or sample_meta.get("answer", "N/A")
        lines.append(f"- **参考答案**: {ref}")
        dur = sample_meta.get("duration")
        if dur:
            lines.append(f"- **视频时长**: {_fmt_seconds(float(dur))} ({dur}s)")
        lines.append(f"- **标注/提示时间基准**: `{annotation_time_base}`")
        if sample_meta.get("reference_time_source"):
            lines.append(
                f"- **数据集提示时间** ({sample_meta.get('reference_time_source')}): "
                f"{_fmt_seconds_list(sample_meta.get('reference_times_sec', []))}"
            )
    elif meta:
        lines.append(f"- **视频**: `{meta.get('video_path', 'N/A')}`")

    lines.append(f"- **报告中的模型时间基准**: `{canonical_time_base}`")
    if annotation_time_base == "dataset_reference_only":
        lines.append(
            "- **注意**: 数据集里的时间提示与模型证据时间不是同一坐标系；"
            "下文的 Planner / Evidence / Final Answer 时间统一按原视频绝对秒（`raw_video_seconds`）呈现。"
        )

    if final:
        lines.append(f"\n**模型最终答案**: {final.get('answer', 'N/A')}")
        conf = final.get("confidence")
        if conf is not None:
            lines.append(f"**置信度**: {conf:.2f}")
        correct = sample_meta.get("correct") if sample_meta else None
        if correct is not None:
            lines.append(f"**评测结果**: {'✅ 正确' if correct else '❌ 错误'}")

    return "\n".join(lines) + "\n"


def _section_timebase_contract(
    meta: Optional[Dict[str, Any]],
    sample_meta: Optional[Dict[str, Any]],
    evidence_payloads: List[Dict[str, Any]],
) -> str:
    annotation_time_base, canonical_time_base = _extract_time_contract(meta, sample_meta, evidence_payloads)

    lines = ["## 时间基准说明 Time-base Contract\n"]
    lines.append(f"- **标注/提示时间基准**: `{annotation_time_base}`")
    lines.append(f"- **报告中的模型时间基准**: `{canonical_time_base}`")
    lines.append(
        "- **统一约定**: 下文出现的观测区间、证据时间、最终 `key_timestamps`，"
        "都按 `raw_video_seconds` 解释，即“从原始视频开头开始计秒”。"
    )

    if sample_meta:
        ref_source = sample_meta.get("reference_time_source")
        ref_times = sample_meta.get("reference_times_sec", [])
        hint_summary = sample_meta.get("temporal_hint_summary", "")
        if ref_source and ref_times:
            lines.append(f"- **数据集提示时间** ({ref_source}): {_fmt_seconds_list(ref_times)}")
        if hint_summary:
            lines.append(f"- **时间提示说明**: {hint_summary}")

    if not evidence_payloads:
        return "\n".join(lines) + "\n"

    lines.append("\n### 媒体输入与时间映射\n")
    lines.append("| 轮次 | 媒体输入 | 原视频绝对范围 | 媒体本地时间基准 | 报告输出时间基准 | 归一化状态 |")
    lines.append("|------|----------|----------------|------------------|------------------|------------|")

    for ev in evidence_payloads:
        round_id = ev.get("round_id", "?")
        model_call = ev.get("model_call", {}) or {}
        normalization = model_call.get("time_normalization", {}) or {}
        canonical_ref = normalization.get("canonical_reference", "raw_video_seconds")
        media_inputs = model_call.get("media_inputs", []) or [{}]

        for media_input in media_inputs:
            input_type = media_input.get("input_type", "unknown")
            abs_interval = _fmt_interval(
                media_input.get("absolute_start_sec"),
                media_input.get("absolute_end_sec"),
            )
            clip_time_base = media_input.get("clip_time_base", "unknown")

            if clip_time_base == "clip_local_seconds":
                if normalization.get("applied"):
                    shift_sec = normalization.get("shift_sec", normalization.get("source_clip_start_sec", 0.0))
                    norm_status = f"后处理换算回原视频秒（+{shift_sec:.1f}s）"
                else:
                    norm_status = "模型已直接返回原视频秒，无需后处理"
            else:
                norm_status = "原样就是原视频秒"

            lines.append(
                f"| R{round_id} | `{input_type}` | {abs_interval} | `{clip_time_base}` | "
                f"`{canonical_ref}` | {norm_status} |"
            )

    return "\n".join(lines) + "\n"


def _section_initial_plan(plan: Optional[Dict]) -> str:
    if not plan:
        return ""
    lines = ["## 初始规划 Initial Plan\n"]
    lines.append(f"- **加载模式**: `{plan.get('watch', {}).get('load_mode', 'N/A')}`")
    regions = plan.get("watch", {}).get("regions", [])
    if regions:
        reg_str = ", ".join(f"[{_fmt_seconds(s)}–{_fmt_seconds(e)}]" for s, e in regions)
        lines.append(f"- **观测区间** (`raw_video_seconds`): {reg_str}")
    fps = plan.get("watch", {}).get("fps")
    if fps:
        lines.append(f"- **帧率 FPS**: {fps}")
    desc = plan.get("description", "")
    if desc:
        lines.append(f"- **描述**: {desc}")
    return "\n".join(lines) + "\n"


def _section_dialogues(traces: List[Dict]) -> str:
    if not traces:
        return ""
    lines = ["## 角色对话详情 Role Dialogues\n"]

    # Group by round
    rounds: Dict[int, List[Dict]] = {}
    for t in traces:
        rid = t.get("round_id", 0)
        rounds.setdefault(rid, []).append(t)

    for rid in sorted(rounds.keys()):
        label = "规划阶段" if rid == 0 else f"第 {rid} 轮"
        lines.append(f"### {label} (Round {rid})\n")
        for t in rounds[rid]:
            role = t.get("role", "unknown")
            ts = t.get("ts", "")
            lines.append(f"#### {_role_icon(role)}")
            lines.append(f"> *时间戳: {ts}*\n")

            prompt = t.get("prompt_text") or ""
            response = t.get("raw_response") or ""
            parsed = t.get("parsed_output")

            if prompt and prompt != "[heuristic — no LLM call]":
                lines.append("<details>")
                lines.append("<summary>📨 Prompt（点击展开）</summary>\n")
                lines.append("```")
                lines.append(_wrap(prompt, width=110))
                lines.append("```")
                lines.append("</details>\n")

            if response and response != "[heuristic — no LLM call]":
                lines.append("<details>")
                lines.append("<summary>📩 Raw Response（点击展开）</summary>\n")
                lines.append("```")
                lines.append(_wrap(response, width=110))
                lines.append("```")
                lines.append("</details>\n")
            elif role.startswith("reflector") and prompt == "[heuristic — no LLM call]":
                lines.append("> 🔀 *启发式决策，无 LLM 调用*\n")

            if parsed and not isinstance(parsed, str):
                lines.append("**解析输出:**")
                lines.append("```json")
                lines.append(json.dumps(parsed, ensure_ascii=False, indent=2))
                lines.append("```\n")
            elif isinstance(parsed, str) and parsed not in ("[heuristic — no LLM call]", ""):
                lines.append(f"**解析输出:** `{parsed}`\n")

            lines.append("---\n")

    return "\n".join(lines)


def _section_evidence_timeline(
    traces: List[Dict],
    run_dir: pathlib.Path,
    evidence_payloads: List[Dict[str, Any]],
) -> str:
    lines = ["## 视频证据时间线 Evidence Timeline\n"]
    lines.append("> 下表时间统一为 `raw_video_seconds`（从原始视频开头开始计秒）。\n")

    # Collect evidence from all rounds
    all_items: List[Dict] = []
    if evidence_payloads:
        for ev in evidence_payloads:
            round_id = ev.get("round_id", "?")
            for item in ev.get("key_evidence", []):
                if isinstance(item, dict):
                    cloned = dict(item)
                    cloned["_round"] = round_id
                    all_items.append(cloned)

    if not all_items:
        # Fallback: parse from observer traces
        for t in traces:
            if t.get("role") == "observer":
                raw = t.get("raw_response", "")
                try:
                    data = json.loads(raw.strip().strip("`").strip("json").strip())
                    for item in data.get("key_evidence", []):
                        item["_round"] = t.get("round_id", "?")
                        all_items.append(item)
                except Exception:
                    pass

    if not all_items:
        lines.append("*未找到结构化证据条目。*\n")
        return "\n".join(lines)

    lines.append("| 轮次 | 时间区间 | 描述 |")
    lines.append("|------|----------|------|")
    for item in all_items:
        ts_s = item.get("timestamp_start")
        ts_e = item.get("timestamp_end")
        if ts_s is not None and ts_e is not None:
            interval = f"`{_fmt_seconds(ts_s)}` – `{_fmt_seconds(ts_e)}`"
        elif ts_s is not None:
            interval = f"`{_fmt_seconds(ts_s)}`"
        else:
            interval = "—"
        desc = (item.get("description") or "").replace("\n", " ").replace("|", "｜")
        rnd = item.get("_round", "?")
        lines.append(f"| R{rnd} | {interval} | {desc} |")

    return "\n".join(lines) + "\n"


def _section_final_answer(final: Optional[Dict]) -> str:
    if not final:
        return ""
    lines = ["## 最终答案 Final Answer\n"]
    lines.append(f"**答案**: {final.get('answer', 'N/A')}\n")
    ev_sum = final.get("evidence_summary", "")
    if ev_sum:
        lines.append(f"**证据摘要**:\n{ev_sum}\n")
    kts = final.get("key_timestamps", [])
    if kts:
        ts_str = ", ".join(f"`{_fmt_seconds(t)}`" for t in kts)
        lines.append(f"**关键时间点** (`raw_video_seconds`): {ts_str}\n")
    conf = final.get("confidence")
    if conf is not None:
        lines.append(f"**置信度**: {conf:.2f}\n")
    return "\n".join(lines)


# ──────────────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────────────

def generate_report(run_dir: pathlib.Path, out_path: pathlib.Path) -> None:
    traces        = _load_jsonl(run_dir / "role_traces.jsonl")
    plan          = _load_json(run_dir / "plan.initial.json")
    final         = _load_json(run_dir / "final_answer.json")
    meta          = _load_json(run_dir / "meta.json")
    sample_meta   = _load_json(run_dir / "sample_metadata.json")
    evidence_payloads = _load_evidence_payloads(run_dir)

    sections = [
        f"# AVP 对话报告 — {run_dir.name}\n",
        _section_overview(meta, sample_meta, final, evidence_payloads),
        _section_timebase_contract(meta, sample_meta, evidence_payloads),
        _section_initial_plan(plan),
        _section_dialogues(traces),
        _section_evidence_timeline(traces, run_dir, evidence_payloads),
        _section_final_answer(final),
    ]

    content = "\n".join(s for s in sections if s)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(content)
    print(f"✅ Report written to: {out_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate AVP dialogue report (Markdown)")
    parser.add_argument("--run-dir", required=True, help="Path to sample run directory")
    parser.add_argument("--out", default=None, help="Output .md path (default: <run-dir>/dialogue_report.md)")
    args = parser.parse_args()

    run_dir = pathlib.Path(args.run_dir).resolve()
    if not run_dir.exists():
        raise SystemExit(f"❌ Run directory not found: {run_dir}")

    out_path = pathlib.Path(args.out).resolve() if args.out else run_dir / "dialogue_report.md"
    generate_report(run_dir, out_path)


if __name__ == "__main__":
    main()
