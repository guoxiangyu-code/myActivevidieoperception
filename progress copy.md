# Progress

- Re-invoked the `planning-with-files` skill for the revised request.
- Re-opened the current planning files and checked session catch-up state.
- Read `dataset/SportsTime/basketball_full_question_first200.json` and confirmed it contains 200 QA records with exactly 6 fields.
- Narrowed the plan to:
  - QA only
  - `basketball_full_question_first200.json`
  - `Qwen3.5-9B` only
  - explicit per-question correctness evaluation
- Rewrote `task_plan.md` and `plan.md` around the new scope.
- Reviewed the current AVP logging paths in `avp/main.py` and updated the plan to require raw per-role traces plus role-contract enforcement.
- Prepared a one-question enriched input at `dataset/SportsTime/basketball_first_question_enriched.json`.
- Added `avp/qwen_first_question_demo.py` to run the first question with local `Qwen3.5-9B`.
- Updated runtime dependencies in `requirements.txt` and installed missing packages inside the `ActiveVideoPerception` conda environment.
- Investigated the `duration` field and confirmed `1839.3666666666666` is the correct full-video duration for `Basketball_Full_001_1.mp4`.
- Fixed the first runtime blocker by installing `torchvision`.
- Fixed the second runtime blocker by reducing observer frame count, resizing images, splitting model memory more safely, and clearing CUDA cache between role calls.
- Fixed the third runtime blocker by improving JSON recovery from Qwen replies that mixed reasoning text with a valid JSON object.
- Completed one end-to-end first-question run and saved all role traces under:
  - `/home/guoxiangyu/pytorch_project/ActiveVideoPerception/avp/out/qwen_first_question_demo/Basketball_Full_001_1_1_20260316_220729`
- Verified that the run produced a judged result with `trace_available: true` and `role_contract_status: passed`.
- Reworked the observer path to support chunked high-resolution viewing on two RTX 3090 GPUs instead of relying on aggressive downscaling.
- Validated a full-resolution chunked run at:
  - `/home/guoxiangyu/pytorch_project/ActiveVideoPerception/avp/out/qwen_first_question_demo/Basketball_Full_001_1_1_20260316_225008`
- Began the next mechanism-level task: make the framework automatically rewatch, relocalize, and reinterpret when evidence is insufficient, instead of stopping after the first incomplete view.
- Confirmed that the original AVP code already has a replanning prompt path in `avp/prompt.py` and a multi-round controller loop in `avp/main.py`.
- Confirmed that the current Qwen prototype is the piece that still needs to be brought back toward the original plan-observe-reflect-replan mechanism.
- Retrospectively recorded the generic controller/generalization change in the project log, including what was planned, what was modified, and what was executed automatically.
- Updated the repo-level instruction so future planning-only requests pause after planning and wait for explicit confirmation before execution.

# Status

- First-question prototype implementation complete.
- First-question execution complete.
- Communication-trace summarization complete.
- Generic rewatch / relocalize controller work in progress.
- Workflow clarified: future work is paused after planning until the user confirms execution.

# Notes

- A small SQL quoting mistake occurred while updating todos and was corrected immediately.
- The current first-question answer is still wrong because the planner/observer only covered the first 60 seconds of the video, which contain pre-game material instead of the foul event.
- The immediate follow-up work is to replace the one-pass stopping logic with a general multi-round controller that keeps watching until answerability is high enough or the search budget is exhausted.
- A retrospective run already validated the multi-round controller mechanism, but further mechanism refinement is paused pending user confirmation.
- Re-invoked `planning-with-files` for a planning-only clarification request and did not run deployment/testing commands.
- Re-read the original AVP implementation surfaces for role/function definitions and contracts:
  - `avp/main.py`
  - `avp/prompt.py`
  - `avp/eval_dataset.py`
  - `avp/eval_parallel.py`
  - `avp/video_utils.py`
- Clarified the five previously ambiguous points in the planning file with explicit code anchors:
  - search budget
  - per-role output contracts
  - QA validation mechanism
  - role definitions and responsibilities
  - observer video-analysis strategy
- Updated the plan to require a separate Judger LLM for semantic correctness decisions in unresolved open-ended QA cases, while keeping deterministic pre-checks.
- Incorporated new user-directed planning constraints:
  - do not automatically reduce FPS in the Qwen path; allow longer processing time instead;
  - do not enforce a rigid full Judger schema; use tolerant correctness extraction from free-form judge outputs.
- Updated `task_plan.md` and mirrored it to `plan.md` with these two policy changes.
