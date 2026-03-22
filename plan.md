# AVP QA plan for SportsTime first200 with Qwen3.5-9B

## Goal

Use ActiveVideoPerception for an open-ended QA task based on `dataset/SportsTime/basketball_full_question_first200.json`, keep `Qwen3.5-9B` as the only planned model backend, and design a reliable way to decide whether each predicted answer is correct.

## Current scope

- Task type: open-ended QA
- Dataset file: `dataset/SportsTime/basketball_full_question_first200.json`
- Model: `Qwen3.5-9B` only
- Environment: `ActiveVideoPerception` conda environment

## Clarified design points (anchored to original AVP code)

### 1) Search budget

- Primary search budget is round-based: `Controller.run(query, max_rounds=...)` (`avp/main.py:1787-1805`), exposed by `--max-turns` in dataset and parallel runners (`avp/eval_dataset.py:387`, `avp/eval_parallel.py:118,279`).
- Per-round action budget is exactly one observation action:
  - `PLAN_SCHEMA.steps` requires exactly one item (`avp/prompt.py:23-25`).
  - Planner takes only the first step (`avp/main.py:598-607`).
- Original AVP code includes per-observation frame caps and adaptive FPS down-adjustment (`avp/main.py:369-371,451-499`).
- User-directed policy update for the Qwen path: do **not** automatically reduce FPS; keep requested FPS and allow longer processing time.
- Practical control strategy under this policy:
  - increase wall-clock timeout/search time budget when needed;
  - prefer chunking and multi-round coverage expansion over FPS downscaling;
  - keep round budget as the first-class controller budget, with coverage tracking (`watched_intervals`, `coverage_ratio`) as a stop condition alongside confidence.

### 2) Output contract for each role

- Planner contract: `PLAN_SCHEMA` (`avp/prompt.py:17-53`), parsed/validated in planning path (`avp/main.py:589-597`).
- Observer contract: `EVIDENCE_SCHEMA` (`avp/prompt.py:56-76`), parsed/validated in observation path (`avp/main.py:946-959`).
- Reflector contract:
  - existing controller currently uses a heuristic reflection dict (`sufficient`, `should_update`, `query_confidence`, etc.) in `Reflector.reflect` (`avp/main.py:1460-1669`);
  - structured `REFLECTION_SCHEMA` is already defined (`avp/prompt.py:79-97`) and should be enforced in the Qwen path.
- Synthesizer contract:
  - current AVP synthesis path is MCQ-shaped (`MCQ_SCHEMA`, `avp/prompt.py:118-131`, `607-665`; validation in `avp/main.py:1225`);
  - for open QA, final proposal is to add/require `QA_SCHEMA` (`answer`, `confidence`, `reasoning`) while preserving MCQ contract for MCQ tasks.
- Judger contract (relaxed): no strict schema requirement; output only needs to clearly indicate **correct/incorrect** (plus optional short rationale). Parsing should accept both JSON-like and free-text replies.

### 3) QA validation mechanism (semantic correctness with a separate judge model)

- Current baseline is insufficient for open QA:
  - parser validation checks only required keys (`avp/prompt.py:1034-1047`);
  - open-ended correctness in dataset eval is simple substring matching (`avp/eval_dataset.py:272-274`).
- Final proposal:
  1. deterministic normalization and exact/rule checks (numbers, counts, time spans);
  2. unresolved cases routed to a separate Judger LLM (not the same inference role) that takes `{question, predicted_answer, reference_answer, CoT}`;
  3. fail-closed judgment extraction via tolerant parsing: accept free-form judge output as long as correctness can be reliably mapped to `correct=true/false`; save raw judge trace for audit.
- `CoT` is judge-only context and never fed to planner/observer/synthesizer generation.

### 4) Definitions and functions of core roles

- Controller: orchestrates the full loop and persistence (`Store`) across rounds (`avp/main.py:1675-1957`).
- Planner: creates one observation action per round (initial or replan) (`avp/main.py:1267-1283`, `529-683`).
- Observer: executes the watch config, queries video, returns structured evidence (`avp/main.py:1287-1419`, `694-1046`).
- Reflector: decides sufficiency / continue-search and produces stop-or-replan decision (`avp/main.py:1422-1670`).
- Synthesizer: converts aggregated evidence into final answer output (`avp/main.py:1182-1245`, `1900-1929`).
- Judger (evaluation role): not in original runtime loop as a first-class class; should be added in evaluation stage to assess semantic correctness and provide auditable scoring rationale.

### 5) Observer video analysis strategy

- AVP strategy is plan-conditioned video observation, not blind full-frame dumping:
  - planner sets `load_mode`, `fps`, `spatial_token_rate`, optional `regions` (`avp/prompt.py:17-53`);
  - observer computes span and executes uniform/region analysis (`avp/main.py:1296-1399`);
  - region mode uses ffmpeg clip extraction when possible (`avp/main.py:738-865`, `avp/video_utils.py:564-682`);
  - timestamps are normalized to full-second intervals (`avp/main.py:986-1016`, `avp/video_utils.py:475-513`).
- Final proposal: keep AVP coarse-to-fine and region-replan logic, keep high-resolution chunking for clarity, and let reflector-driven replanning expand coverage until confidence or budget stop criteria are met.

## Findings that drive the plan

- The chosen JSON file contains 200 records and exactly 6 fields:
  - `id`
  - `video_id`
  - `task_type`
  - `question`
  - `CoT`
  - `answer`
- This QA file does not contain AVP-ready runtime fields like `path` or `duration`, so it must be enriched from the local dataset folders before AVP can run on it.
- The local basketball media folders are still necessary because they likely provide the real video files matched by `video_id`.
- The user has narrowed the backend requirement to `Qwen3.5-9B`; do not plan around `Qwen3-VL`.
- Because this dataset is open-ended QA rather than multiple choice, correctness scoring must be redesigned; a naive substring match is not enough.
- The current AVP runtime already persists parsed artifacts such as `plan.initial.json`, round-level `evidence.json`, `history.jsonl`, `conversation_history.json`, and `final_answer.json`, but it does not yet guarantee a complete raw reply archive for every role on every question.
- The current single-question Qwen prototype still behaves like a one-shot observation system: it plans one region, observes that region, and synthesizes an answer even when the observed content is clearly insufficient for answering the question.
- The current reflector shortcut in the prototype only checks whether evidence exists, not whether the evidence is actually sufficient to answer the question with high confidence.
- To preserve generalization, the next iteration must improve the underlying control mechanism rather than hard-code behavior for this specific basketball example.

## Implementation plan

### Phase 1 - Build an enriched QA annotation set

1. Treat `dataset/SportsTime/basketball_full_question_first200.json` as the source list of questions.
2. Match each `video_id` to the corresponding local basketball video file.
3. Create a generated AVP-ready annotation file that adds:
   - `path`
   - `duration`
   - `question`
   - `answer`
   - `task_type`
   - any extra bookkeeping fields needed for evaluation
4. Keep the original `CoT` in the enriched file as a reference-only field for evaluation or error analysis, not as inference input.

### Phase 2 - Refactor AVP for Qwen3.5-9B only

5. Replace the Gemini-specific client path with a local `Qwen3.5-9B` path.
6. Preserve the AVP paper's structure:
   - planner proposes what to inspect
   - observer inspects targeted video evidence
   - reflector decides whether the evidence is sufficient
7. Use `Qwen3.5-9B` for all stages rather than splitting roles across different models.
8. Add or update config fields so runs can point to `/home/guoxiangyu/.cache/modelscope/hub/models/Qwen/Qwen3.5-9B`.

### Phase 3 - Make the answering path QA-friendly

9. Ensure the runtime returns a free-form predicted answer for every question.
10. Store one structured result per question with:
   - `id`
   - `video_id`
   - `question`
   - `predicted_answer`
   - `reference_answer`
   - correctness metadata

### Phase 4 - Record every agent reply for every tested question

11. For each question run, save the raw prompt and raw response of every role:
   - planner
   - observer
   - reflector
   - synthesizer
12. Keep both parsed outputs and raw outputs so debugging can answer: "what exactly did each agent say?"
13. Extend the run directory with per-question trace files such as:
   - `planner.prompt.txt`
   - `planner.raw_response.txt`
   - `observer.prompt.txt`
   - `observer.raw_response.txt`
   - `reflector.prompt.txt`
   - `reflector.raw_response.txt`
   - `synthesizer.prompt.txt`
   - `synthesizer.raw_response.txt`
14. Also add structured trace metadata to `history.jsonl`, including:
   - role name
   - round ID
   - schema validation status
   - retry count
   - file path of the raw response

### Phase 5 - Enforce role-specific response contracts

15. Define an explicit response contract for each role:
    - planner must return a valid planning schema
    - observer must return valid evidence schema
    - reflector must return a valid sufficiency / replan decision schema
    - synthesizer must return the final QA answer schema
    - judger only needs to return an interpretable correctness signal (correct/incorrect), with optional rationale
16. Validate planner/observer/reflector/synthesizer with strict schemas; parse judger output with a tolerant correctness extractor.
17. If a role reply is invalid, retry with role-specific repair instructions instead of silently accepting malformed output.
18. If repeated retries still fail, mark the question as `role_contract_failed` rather than pretending the role behaved correctly.
19. Treat this as an operational guarantee:
   - not "the model can never make a wrong-format reply"
   - but "the system will detect invalid role behavior, retry it, and fail closed if it cannot recover"

### Phase 6 - Decide correctness for every question

20. Implement a dedicated correctness evaluator with a separate Judger LLM (not the planner/observer model path), and do not require a rigid full JSON schema from the Judger.
21. Score each answer in stages:
    - exact match after normalization
    - specialized matching for numbers, counts, ranges, and time expressions
    - semantic judgment for answers that are paraphrased but still correct
22. For semantic judgment, compare the predicted answer against:
    - the question
    - the ground-truth `answer`
    - the reference `CoT` as supporting context for judging, not generation
23. Save per-question evaluation outputs such as:
   - `correct`
   - `score_method`
   - `judge_reason`
   - `normalized_prediction`
   - `normalized_reference`
   - `role_contract_status`
   - `trace_available`

### Phase 7 - Validate the pipeline

24. Run a smoke test on 5-10 questions.
25. Manually inspect a small calibration set to verify both:
   - answer correctness labels
   - whether each role trace is fully captured and valid
26. Then run the full 200-question file and summarize:
   - overall accuracy
   - per-task-type accuracy
   - common failure patterns
    - role-contract failures
    - missing or malformed role traces

### Phase 8 - Add a generic rewatch / relocalize / reinterpret loop

27. Replace the current one-pass prototype control flow with a generic multi-round controller.
28. Preserve the original AVP architecture at the mechanism level:
    - plan or replan what to inspect
    - observe the proposed region or scan
    - reflect on whether the gathered evidence is sufficient to answer the question
    - if not sufficient, rewatch with a new region or broader scan
29. Make the stop condition mechanism-driven rather than example-driven:
    - stop when the system has a high-confidence answer with supporting evidence
    - otherwise keep watching the video until the round budget or full-video coverage is exhausted
30. Ensure the relocalization logic is generic:
    - expand from local region to broader region when the target event is missing
    - switch from narrow region to uniform/full-video scan when early localization fails
    - support reinterpretation after new evidence arrives instead of locking the answer too early
31. Avoid question-specific heuristics tied to this basketball foul example; any new logic should work as a reusable controller policy for open-ended video QA.
32. Persist round-by-round state so it is clear:
    - what region was watched
    - why the next region was chosen
    - whether the answerability confidence increased
    - why the controller stopped

## Risks and considerations

- The six-field question file is incomplete for AVP runtime use and must be enriched.
- Open-ended answer grading is a major source of noise if normalization and semantic judging are not handled carefully.
- `Qwen3.5-9B` video-input behavior after local preprocessing is the largest technical integration risk.
- A prompt alone cannot absolutely guarantee that each role will always answer in the intended format; the guarantee must come from validation, retries, and fail-closed handling.

## Current implementation checkpoint

- A single-question prototype now exists at `avp/qwen_first_question_demo.py`.
- The first QA sample has been executed successfully end to end in the `ActiveVideoPerception` conda environment.
- The latest successful trace directory is:
  - `/home/guoxiangyu/pytorch_project/ActiveVideoPerception/avp/out/qwen_first_question_demo/Basketball_Full_001_1_1_20260316_220729`
- The run now saves prompt/response traces for:
  - planner
  - observer
  - reflector
  - synthesizer
  - judge
- Role-contract validation passed for the completed single-question run.
- The observer path now supports chunked high-resolution viewing on two RTX 3090 GPUs, including a validated full-resolution path with original 1280x720 frames processed in small chunks.
- A retrospective multi-round controller change has been recorded and logged for:
  - `/home/guoxiangyu/pytorch_project/ActiveVideoPerception/avp/out/qwen_first_question_demo/Basketball_Full_001_1_1_20260316_230838`
- That run confirmed the controller can now continue across rounds instead of stopping immediately after the first insufficient segment.
- The answer is still incorrect because replanning quality remains weak even though the continuation mechanism now exists.
- The next practical improvement is now controller policy quality:
  - relocate more intelligently after insufficient evidence
  - improve generic replan quality without hard-coding this basketball example
  - only finalize the answer when confidence is high enough or search coverage is exhausted

## Workflow constraint

- After `planning-with-files` or any planning-only request, do not execute commands or code changes until the user explicitly confirms.
- If that rule was violated earlier in the session, keep the history by adding a retrospective log entry instead of silently rewriting prior logs.

## Errors encountered

| Error | Attempt | Resolution |
| --- | --- | --- |
| SQL syntax error while updating todo text with an apostrophe | 1 | Rewrote the SQL text without the problematic quote and continued |
| `AutoProcessor.from_pretrained(...)` failed because `torchvision` was missing | 1 | Installed `torchvision`, updated `requirements.txt`, and retried |
| Observer generation hit CUDA OOM with too many large frames | 2 | Reduced sampled frames, resized images, used balanced multi-GPU loading, and added cache cleanup |
| Qwen replies often mixed reasoning text with valid JSON | 3 | Tightened retry prompts and improved JSON extraction to recover the largest valid balanced JSON object |
| Temporal-window observer OOM when a 30s window at 2.0 fps delivered 60 full-resolution frames | 4 | Added `--observer-window-max-frames` with uniform within-window subsampling (default 16), verified on a smoke run, and relaunched the 5-question batch |

## Latest status update

- 2026-03-20: Wrote `/home/guoxiangyu/pytorch_project/ActiveVideoPerception/local_qwen_vs_original_avp_analysis.md`.
- The report compares the original AVP code path (`avp/main.py`, `avp/prompt.py`, `avp/eval_dataset.py`) against the local Qwen open-QA path (`avp/qwen_first_question_demo.py`, `avp/qwen_batch_eval.py`).
- The report also references runtime artifacts at `avp/out/qwen_batch_eval/batch_20260319_083607/` and the Q4 rerun directory `avp/out/qwen_first_question_demo/Basketball_Full_001_1_5_20260319_101524/`.
- No new model execution was launched for this documentation step; this step only wrote the analysis artifact and synchronized logs.
- 2026-03-20: Implemented temporal-window observer chunking in `avp/qwen_first_question_demo.py` and exposed `--observer-window-sec` so each observer call now sees planner-sampled frames from a continuous `k`-second interval instead of a fixed number of consecutive frames.
- The local defaults were updated to favor the new calibration policy: `observer_window_sec=30.0`, legacy `observer_chunk_size=0`, and `max_rounds=30` in both single-question and batch runners.
- Verified on partial smoke run `/home/guoxiangyu/pytorch_project/ActiveVideoPerception/avp/out/qwen_first_question_demo/Basketball_Full_001_1_1_20260320_120600` that `observer.round01.chunk01.frame_metadata.json` records `chunk_mode=temporal_window`, `start_sec=0.0`, `end_sec=30.0`, and `frame_count=60`.
- Launched a new expanded-budget 5-question batch at `/home/guoxiangyu/pytorch_project/ActiveVideoPerception/avp/out/qwen_batch_eval/batch_20260320_121122`; first-question metadata already shows `max_rounds=30`, `observer_window_sec=30.0`, `max_frames=0`, and a 60-second planner span sampled into 120 frames.
- The expanded 5-question batch is still running; no final accuracy summary is available yet from this new batch.
- After the first expanded-budget batch launch (`batch_20260320_121122`), `logs/q0000.log` showed a new CUDA OOM because the first 30-second observer window kept all 60 frames from a `2.0 fps` planner span.
- Added an explicit safety control `observer_window_max_frames=16` so the observer still sees frames spanning the full 30-second interval, but only a uniformly distributed subset is sent to the model in each time window.
- Verified on smoke run `/home/guoxiangyu/pytorch_project/ActiveVideoPerception/avp/out/qwen_first_question_demo/Basketball_Full_001_1_1_20260320_121542` that `observer.round01.chunk01.frame_metadata.json` records `chunk_mode=temporal_window`, `start_sec=0.0`, `end_sec=30.0`, `source_frame_count=60`, and `frame_count=16`.
- Relaunched the 5-question expanded-budget batch at `/home/guoxiangyu/pytorch_project/ActiveVideoPerception/avp/out/qwen_batch_eval/batch_20260320_122105`; the first-question metadata already records `observer_window_max_frames=16`.
- Real-time check: the restarted batch is still on question 0; batch log files have not been flushed yet because `q0000.log` is only written after the question finishes.
- Real-time check: current run directory `/home/guoxiangyu/pytorch_project/ActiveVideoPerception/avp/out/qwen_first_question_demo/Basketball_Full_001_1_1_20260320_122105` shows completed artifacts through round 6 and partial round-7 observer files, so question 0 is currently in round 7 (observer stage, chunk 3 not yet parsed).
- Real-time GPU check: one Python process is occupying both RTX 3090s simultaneously (`~12.2 GiB` on GPU0 and `~14.7 GiB` on GPU1), which means the current runtime is using balanced multi-GPU sharding rather than one independent task per card.

- 2026-03-20: Halted the running Qwen batch at the user's request and pivoted testing preparation to Gemini 2.5 Pro.
- Added Gemini custom endpoint support to the original AVP path: `AVPConfig` now accepts `base_url`, `GeminiClient` accepts `base_url` / `GEMINI_BASE_URL`, and `eval_dataset.py` passes that field into the runtime client.
- Verified locally (without a live network call) that `GeminiClient(model='gemini-2.5-pro', api_key='DUMMY_KEY', base_url='https://yunwu.ai/v1')` initializes successfully through the installed `google-genai` SDK.
- Generated `/home/guoxiangyu/pytorch_project/ActiveVideoPerception/dataset/SportsTime/basketball_first5_enriched.json` for Gemini-side first-5 testing and `/home/guoxiangyu/pytorch_project/ActiveVideoPerception/avp/config.gemini_yunwu_first5.example.json` as a non-secret config template.
- Important security constraint: the provided API key was not written into repository files or logs; the intended usage path is environment-variable based (`GEMINI_API_KEY`, `GEMINI_BASE_URL`).
- Important environment constraint: this shell environment cannot make outbound internet API calls, so the code path is prepared and dry-run initialized, but a live Gemini request was not executed here.

- 2026-03-20: Prepared safe local-run instructions for Gemini/Yunwu testing, including how to pass `GEMINI_API_KEY` and `GEMINI_BASE_URL` via the shell and how to run only the second question by generating a one-sample temporary annotation outside the repository.

- 2026-03-20: Reconstructed the actual Gemini Q2 run into a standalone Markdown report and revised the original Gemini synthesis path so open-ended QA now targets `FINAL_ANSWER_SCHEMA` instead of using placeholder MCQ option `A`. Local compile and schema-path validation passed; live re-test remains blocked in this shell because outbound Gemini API calls are unavailable.
