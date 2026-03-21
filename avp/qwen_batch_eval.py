#!/usr/bin/env python3
"""
Batch evaluator for SportsTime QA using the in-process Qwen AVP-style runner.

Unlike subprocess-based orchestration, this script keeps the Qwen model loaded
in one Python process (via qwen_first_question_demo runner cache), which avoids
reloading model weights for every question.
"""

from __future__ import annotations

import argparse
import json
import signal
import time
import traceback
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, List

from . import qwen_first_question_demo as qwen_runner


def now_stamp() -> str:
    return time.strftime("%Y%m%d_%H%M%S", time.localtime())


def append_jsonl(path: Path, obj: Dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")


@contextmanager
def question_timeout(seconds: int) -> Iterator[None]:
    """Apply per-question timeout on Unix platforms; no-op otherwise."""
    if seconds <= 0 or not hasattr(signal, "SIGALRM"):
        yield
        return

    def _handler(_signum: int, _frame: Any) -> None:
        raise TimeoutError(f"Question timed out after {seconds}s")

    prev_handler = signal.getsignal(signal.SIGALRM)
    signal.signal(signal.SIGALRM, _handler)
    signal.alarm(seconds)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, prev_handler)


def build_runner_argv(args: argparse.Namespace, question_index: int) -> List[str]:
    return [
        "--question-file",
        args.question_file,
        "--dataset-root",
        args.dataset_root,
        "--question-index",
        str(question_index),
        "--model-path",
        args.model_path,
        "--out",
        args.sample_out,
        "--max-frames",
        str(args.max_frames),
        "--image-max-side",
        str(args.image_max_side),
        "--observer-chunk-size",
        str(args.observer_chunk_size),
        "--observer-window-sec",
        str(args.observer_window_sec),
        "--observer-window-max-frames",
        str(args.observer_window_max_frames),
        "--max-rounds",
        str(args.max_rounds),
        "--uniform-window-sec",
        str(args.uniform_window_sec),
        "--answer-confidence-threshold",
        str(args.answer_confidence_threshold),
    ]


def run_one_question(
    args: argparse.Namespace,
    question_index: int,
    logs_dir: Path,
) -> Dict[str, Any]:
    argv = build_runner_argv(args, question_index)
    log_file = logs_dir / f"q{question_index:04d}.log"
    started_at = time.time()

    try:
        with question_timeout(args.timeout_per_question):
            summary = qwen_runner.main(argv)
        elapsed = time.time() - started_at

        if not isinstance(summary, dict):
            raise RuntimeError("Runner returned a non-dict summary.")
        if "question_id" not in summary or "predicted_answer" not in summary:
            raise RuntimeError("Runner summary missing required keys.")

        log_file.write_text(
            "STATUS: SUCCESS\n"
            f"elapsed_seconds={elapsed:.1f}\n"
            f"argv={' '.join(argv)}\n\n"
            "=== SUMMARY ===\n"
            f"{json.dumps(summary, ensure_ascii=False, indent=2)}\n",
            encoding="utf-8",
        )
        return {
            "question_index": question_index,
            "success": True,
            "timed_out": False,
            "elapsed_seconds": elapsed,
            "summary": summary,
            "log_file": str(log_file),
        }
    except TimeoutError as exc:
        elapsed = time.time() - started_at
        log_file.write_text(
            "STATUS: TIMEOUT\n"
            f"elapsed_seconds={elapsed:.1f}\n"
            f"argv={' '.join(argv)}\n\n"
            f"{exc}\n",
            encoding="utf-8",
        )
        return {
            "question_index": question_index,
            "success": False,
            "timed_out": True,
            "elapsed_seconds": elapsed,
            "error": str(exc),
            "log_file": str(log_file),
        }
    except Exception as exc:  # noqa: BLE001
        elapsed = time.time() - started_at
        tb = traceback.format_exc()
        log_file.write_text(
            "STATUS: ERROR\n"
            f"elapsed_seconds={elapsed:.1f}\n"
            f"argv={' '.join(argv)}\n\n"
            f"{exc}\n\n"
            "=== TRACEBACK ===\n"
            f"{tb}\n",
            encoding="utf-8",
        )
        return {
            "question_index": question_index,
            "success": False,
            "timed_out": False,
            "elapsed_seconds": elapsed,
            "error": str(exc),
            "log_file": str(log_file),
        }


def main() -> None:
    parser = argparse.ArgumentParser(description="Batch-run SportsTime QA with in-process Qwen runner.")
    parser.add_argument(
        "--question-file",
        default="dataset/SportsTime/basketball_full_question_first200.json",
        help="SportsTime QA JSON path.",
    )
    parser.add_argument(
        "--dataset-root",
        default="dataset/Basketball/Full",
        help="Root directory containing basketball video folders.",
    )
    parser.add_argument(
        "--model-path",
        default="/home/guoxiangyu/.cache/modelscope/hub/models/Qwen/Qwen3.5-9B",
        help="Local Qwen3.5-9B model path.",
    )
    parser.add_argument(
        "--out",
        default="avp/out/qwen_batch_eval",
        help="Base output directory for batch-level artifacts.",
    )
    parser.add_argument(
        "--sample-out",
        default="avp/out/qwen_first_question_demo",
        help="Output root for per-question traces.",
    )
    parser.add_argument("--start-index", type=int, default=0, help="Start index in QA file.")
    parser.add_argument("--num-questions", type=int, default=200, help="Number of questions to run.")
    parser.add_argument(
        "--timeout-per-question",
        type=int,
        default=0,
        help="Per-question timeout in seconds. 0 disables timeout.",
    )
    parser.add_argument(
        "--max-frames",
        type=int,
        default=0,
        help="Per-question runner arg; 0 disables frame capping (no automatic FPS reduction).",
    )
    parser.add_argument("--image-max-side", type=int, default=0, help="Per-question runner arg.")
    parser.add_argument("--observer-chunk-size", type=int, default=0, help="Legacy per-question runner arg.")
    parser.add_argument("--observer-window-sec", type=float, default=30.0, help="Per-question runner arg.")
    parser.add_argument("--observer-window-max-frames", type=int, default=16, help="Per-question runner arg.")
    parser.add_argument("--max-rounds", type=int, default=30, help="Per-question runner arg.")
    parser.add_argument("--uniform-window-sec", type=float, default=180.0, help="Per-question runner arg.")
    parser.add_argument(
        "--answer-confidence-threshold",
        type=float,
        default=0.75,
        help="Per-question runner arg.",
    )
    args = parser.parse_args()

    questions = json.loads(Path(args.question_file).read_text(encoding="utf-8"))
    if not isinstance(questions, list):
        raise ValueError(f"Question file must be a JSON array: {args.question_file}")

    total_questions = len(questions)
    if args.start_index < 0 or args.start_index >= total_questions:
        raise ValueError(f"start-index out of range: {args.start_index}, total={total_questions}")
    if args.num_questions <= 0:
        raise ValueError("num-questions must be positive")

    end_index = min(total_questions, args.start_index + args.num_questions)
    indices = list(range(args.start_index, end_index))

    batch_dir = Path(args.out) / f"batch_{now_stamp()}"
    batch_dir.mkdir(parents=True, exist_ok=True)
    logs_dir = batch_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    results_path = batch_dir / "results.jsonl"

    print(
        f"Starting in-process batch run: {len(indices)} question(s), "
        f"indices=[{indices[0]}..{indices[-1]}], "
        f"batch_dir={batch_dir}"
    )

    total = 0
    success_count = 0
    correct_count = 0
    timeout_count = 0
    error_count = 0
    started_batch = time.time()

    for rank, q_idx in enumerate(indices, start=1):
        print(f"[{rank}/{len(indices)}] Running question_index={q_idx} ...")
        result = run_one_question(args=args, question_index=q_idx, logs_dir=logs_dir)
        append_jsonl(results_path, result)
        total += 1

        if result.get("success"):
            success_count += 1
            summary = result["summary"]
            if bool(summary.get("correct", False)):
                correct_count += 1
            print(
                f"  success | elapsed={result['elapsed_seconds']:.1f}s | "
                f"correct={summary.get('correct')} | "
                f"running_acc={correct_count}/{success_count}"
            )
        else:
            if result.get("timed_out"):
                timeout_count += 1
                print(f"  timeout | elapsed={result['elapsed_seconds']:.1f}s")
            else:
                error_count += 1
                print(f"  failed | error={result.get('error')}")

    elapsed_batch = time.time() - started_batch
    accuracy = (correct_count / success_count) if success_count > 0 else 0.0
    summary = {
        "question_file": args.question_file,
        "dataset_root": args.dataset_root,
        "start_index": args.start_index,
        "end_index_exclusive": end_index,
        "requested": len(indices),
        "processed": total,
        "successful": success_count,
        "timeouts": timeout_count,
        "errors": error_count,
        "correct": correct_count,
        "accuracy_on_successful": accuracy,
        "elapsed_seconds": elapsed_batch,
        "max_frames": args.max_frames,
        "image_max_side": args.image_max_side,
        "observer_chunk_size": args.observer_chunk_size,
        "observer_window_sec": args.observer_window_sec,
        "observer_window_max_frames": args.observer_window_max_frames,
        "max_rounds": args.max_rounds,
        "uniform_window_sec": args.uniform_window_sec,
        "answer_confidence_threshold": args.answer_confidence_threshold,
        "timeout_per_question": args.timeout_per_question,
        "results_jsonl": str(results_path),
        "logs_dir": str(logs_dir),
    }
    (batch_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
