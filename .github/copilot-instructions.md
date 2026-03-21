# Copilot instructions for ActiveVideoPerception

## Build, test, and validation commands

This repository does not define a dedicated lint target, unit-test suite, or CI workflow. Validation is done through the runtime entry points below.

Environment setup from `README.md`:

```bash
conda create -n avp python=3.10 -y
conda activate avp
conda install -c conda-forge ffmpeg
pip install -r requirements.txt
```

Quickest scoped validation run:

```bash
python -m avp.eval_dataset \
  --ann avp/eval_anno/eval_lvbench.json \
  --out avp/out/debug_single \
  --config avp/config.example.json \
  --limit 1 \
  --max-turns 1 \
  --timeout 600
```

Parallel evaluation entry point used by the repo:

```bash
python -m avp.eval_parallel \
  --ann avp/eval_anno/eval_lvbench.json \
  --out avp/out \
  --config avp/config.example.json \
  --max-turns 3 \
  --num-workers 2 \
  --limit 10 \
  --timeout 2000
```

Script wrapper:

```bash
bash avp/parrelel_run.sh
```

Lower-level debugging entry points from `avp/main.py`:

```bash
python -m avp.main plan --run-dir runs/demo --video /path/to/video.mp4 --query "..."
python -m avp.main run --run-dir runs/demo --video /path/to/video.mp4 --query "..." --max-rounds 3
python -m avp.main show --run-dir runs/demo
```

Before running evaluation, fill in a real config based on `avp/config.example.json`. Credentials are stored in `api_key_config.txt` (gitignored locally) as shell exports — source it before running:

```bash
source api_key_config.txt  # sets GEMINI_API_KEY and GEMINI_BASE_URL
```

Auth options (in priority order):
- **API key + custom endpoint**: `GEMINI_API_KEY` + `GEMINI_BASE_URL` (or `api_key` / `base_url` in config)
- **Google AI Studio**: `GEMINI_API_KEY` only
- **Vertex AI**: `project` + `location` in config (or `VERTEX_PROJECT` / `VERTEX_LOCATION` env vars)

Prefer `avp.eval_dataset` / `avp.eval_parallel` for normal runs. The lower-level `avp.main` CLI does not set annotation metadata for you, so metadata-dependent behavior is easiest to debug through the dataset runners.

## High-level architecture

- `avp/main.py` is the core engine. `Controller` orchestrates the iterative plan -> observe -> reflect loop, persists artifacts with `Store`, and writes per-run JSON outputs such as `plan.initial.json`, `evidence/round_*/evidence.json`, `history.jsonl`, `conversation_history.json`, and `final_answer.json`.
- `avp/prompt.py` centralizes prompt templates and JSON schemas. The runtime is intentionally single-action per round: the planning schema requires exactly one observation step, and downstream code assumes that contract.
- `avp/video_utils.py` owns video-specific helpers: metadata loading, compressed-video fallback lookup, MIME/type handling, ffmpeg clip creation, interval rounding, and clip cleanup.
- `avp/eval_dataset.py` is the dataset-level runner. It loads config, calls `set_metadata_source(ann_path)`, creates one `Controller` per sample, runs the plan/observe/reflect loop, writes `results.jsonl` incrementally, and emits a `summary.json` plus per-sample artifacts under `all_sample/sample_*`.
- `avp/eval_parallel.py` is the batch orchestrator. It splits an annotation JSON into chunks, launches `python -m avp.eval_dataset` in worker subprocesses, stores worker logs/results, and merges them into a final `results.jsonl` and `summary.json`.

## Key repository conventions

- Treat the annotation JSON as part of the runtime contract, not just input data. The code expects fields such as `path` (video file path), `question`, `options`, `duration`, and usually `solution` with `<answer>...</answer>`. The `answer` field holds only the letter (e.g. `"D"`); `solution` wraps it in XML tags (`<answer>D</answer>`). Video duration comes from the annotation JSON via `set_metadata_source`, not from probing the video file.
- Preserve the structured-output workflow when changing prompts or inference logic. `PromptManager`, `parse_json_response`, and schema validation in `avp/prompt.py` are tightly coupled to the `PlanSpec`, `Evidence`, and final-answer parsing logic in `avp/main.py` and `avp/eval_dataset.py`.
- Keep timestamp handling compatible with the current evidence format. Evidence uses `timestamp_start` / `timestamp_end` ranges that are normalized and clamped to full seconds, and persisted alongside the derived `interval_map`.
- Be aware that `AVPConfig.location` is a list and `get_random_location()` chooses one location per sample. If you need deterministic behavior, do not assume a single static location unless you explicitly constrain the config.
- `AVPConfig` supports separate models for planning and execution via `plan_replan_model` and `execute_model` fields. When both are empty the legacy `model` field is used as fallback. This split allows a lighter model for video observation and a heavier one for planning/synthesis.
- `Controller` may transparently switch to a compressed version of a video through `find_compressed_video_fallback()`. The `prefer_compressed` config field (default `true`) controls whether this fallback is attempted. Path-related changes should stay compatible with both original and compressed naming patterns.
- Temp clips are isolated per run under `run_dir/temp_clips`, and `eval_dataset.py` cleans them up after every sample. This isolation is important for parallel evaluation; do not move clip generation to a shared global temp directory.
- The repository is published but not actively supported according to `CONTRIBUTING.md`. Prefer small, surgical changes and avoid introducing new dependencies or large-scale refactors unless the task explicitly asks for them.
- `avp/qwen_batch_eval.py` and `avp/qwen_first_question_demo.py` are an alternative runner for local Qwen models. Unlike the Gemini path, the Qwen runner keeps the model loaded in-process across samples to avoid repeated weight reloads. This runner has its own evaluation loop and does not go through `eval_dataset.py` or `eval_parallel.py`.

## Change logging requirement

- After every code change, append a detailed entry to `copilot_change_log.jsonl` in the repository root.
- For code changes, the log entry must include enough detail to reconstruct both the problem and the fix:
  - `planned`
  - `modified`
  - `auto_executed`
  - `root_cause`
  - `symptom`
  - what changed before vs. after
  - why each code change was made
  - `verification`
  - `recovery_notes`
- Keep the log append-only. If an older short entry needs clarification, add a new entry that references the earlier `change_id` instead of silently removing history.
- If work begins from `planning-with-files` or another explicit planning-only request, stop after updating the planning files and wait for user confirmation before running commands, tests, or code changes.
- If any automatic execution happened before confirmation, append a retrospective log entry that lists the exact plan, the files modified, and the commands that were executed automatically.
