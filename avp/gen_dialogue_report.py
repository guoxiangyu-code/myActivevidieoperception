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
from typing import Any, Dict, List, Optional


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


# ──────────────────────────────────────────────────────────────────────────────
# Section builders
# ──────────────────────────────────────────────────────────────────────────────

def _section_overview(meta: Optional[Dict], sample_meta: Optional[Dict],
                      final: Optional[Dict]) -> str:
    lines = ["## 概览 Overview\n"]

    if sample_meta:
        lines.append(f"- **视频 ID**: `{sample_meta.get('video_id', 'N/A')}`")
        lines.append(f"- **问题**: {sample_meta.get('question', 'N/A')}")
        ref = sample_meta.get("reference_answer") or sample_meta.get("answer", "N/A")
        lines.append(f"- **参考答案**: {ref}")
        dur = sample_meta.get("duration")
        if dur:
            lines.append(f"- **视频时长**: {_fmt_seconds(float(dur))} ({dur}s)")
    elif meta:
        lines.append(f"- **视频**: `{meta.get('video_path', 'N/A')}`")

    if final:
        lines.append(f"\n**模型最终答案**: {final.get('answer', 'N/A')}")
        conf = final.get("confidence")
        if conf is not None:
            lines.append(f"**置信度**: {conf:.2f}")
        correct = sample_meta.get("correct") if sample_meta else None
        if correct is not None:
            lines.append(f"**评测结果**: {'✅ 正确' if correct else '❌ 错误'}")

    return "\n".join(lines) + "\n"


def _section_initial_plan(plan: Optional[Dict]) -> str:
    if not plan:
        return ""
    lines = ["## 初始规划 Initial Plan\n"]
    lines.append(f"- **加载模式**: `{plan.get('watch', {}).get('load_mode', 'N/A')}`")
    regions = plan.get("watch", {}).get("regions", [])
    if regions:
        reg_str = ", ".join(f"[{_fmt_seconds(s)}–{_fmt_seconds(e)}]" for s, e in regions)
        lines.append(f"- **观测区间**: {reg_str}")
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


def _section_evidence_timeline(traces: List[Dict], run_dir: pathlib.Path) -> str:
    lines = ["## 视频证据时间线 Evidence Timeline\n"]

    # Collect evidence from all rounds
    ev_dir = run_dir / "evidence"
    all_items: List[Dict] = []
    if ev_dir.exists():
        for ev_file in sorted(ev_dir.glob("*.json")):
            ev = _load_json(ev_file)
            if ev:
                round_id = ev.get("round_id", "?")
                for item in ev.get("key_evidence", []):
                    if isinstance(item, dict):
                        item["_round"] = round_id
                        all_items.append(item)

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
        lines.append(f"**关键时间点**: {ts_str}\n")
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

    sections = [
        f"# AVP 对话报告 — {run_dir.name}\n",
        _section_overview(meta, sample_meta, final),
        _section_initial_plan(plan),
        _section_dialogues(traces),
        _section_evidence_timeline(traces, run_dir),
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
