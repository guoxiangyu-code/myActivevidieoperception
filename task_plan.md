# AVP unified time-reference bugfix

## Goal

Maintain the unified time-reference bugfix work and design a feasible `Qwen3-Omni` replacement plan for the current Gemini-based AVP runtime, using `ModelScope API` first for validation and keeping local deployment as a later option sized to this machine's compute budget.

Canonical target:
- internal reasoning time base = `raw_video_seconds`

External time sources that must be normalized:
- raw-video absolute times
- clip-local times created by ffmpeg region clipping
- dataset reference times (`cot_reference`, `time_reference`)

---

## Current Phase

Phase 7 — Completed design for a `Qwen3-Omni` replacement plan for the current Gemini path

---

## Phases

### Phase 1: Establish time-base contract
- [x] Define explicit per-sample time-base metadata
- [x] Distinguish `raw_video_seconds`, `clip_local_seconds`, `dataset_reference_only`
- [x] Persist this metadata into run artifacts
- **Status:** complete

### Phase 2: Propagate time context through runtime
- [x] Thread time metadata from `eval_dataset.py` into `Controller`
- [x] Expose temporal disclaimers to Planner and Observer prompts
- [x] Persist source video path, resolved video path, and actual clip path used for model calls
- **Status:** complete

### Phase 3: Enforce unified canonical timestamps
- [x] Add explicit clip-local → raw-video conversion instructions in Observer prompt
- [x] Ensure multi-clip prompt text gives per-clip absolute mapping
- [x] Ensure downstream synthesis explicitly treats evidence timestamps as canonical raw-video seconds
- [x] Verify that Observer / Reflector / Synthesizer all reason over the same canonical timeline on Q2
- **Status:** complete

### Phase 4: Correct stopping logic
- [x] Remove duplicate evidence insertion
- [x] Block reflector success when evidence explicitly says the decisive event lies outside the watched window
- [x] Re-run a failure-prone question to confirm the stop condition is improved
- **Status:** complete

### Phase 5: Validation and regression check
- [x] Re-run Q2 with preserved temp clips and full debug logs
- [x] Copy the actual Q2 analysis clip to a stable preserved-media path
- [x] Validate Q4 against the new unified time-reference behavior
- [x] Validate Q5 against the new unified time-reference behavior
- [x] Record final findings and remaining limitations
- **Status:** complete

### Phase 6: Duration-answer semantics
- [ ] Ensure duration questions default to canonical `raw_video_seconds`, not scoreboard clock, unless the query explicitly asks for game-clock time
- [ ] Re-run Q5 after synthesis guidance is updated
- **Status:** in_progress

### Phase 7: Plan local Qwen replacement
- [x] Inspect local GPU / CPU / RAM / storage and identify the feasible `Qwen3-Omni` deployment envelope
- [x] Trace the current Gemini integration points and decide the cleanest replacement seam
- [x] Choose the most appropriate `Qwen3-Omni` variant and serving path, prioritizing `ModelScope API` for the first validation pass
- [x] Write a migration design covering prompt compatibility, media preprocessing, structured outputs, secret handling, and validation
- **Status:** complete

---

## Key Findings

1. `Q2` now reruns successfully at `avp/out/q2_rerun_debug_20260321_153551`.
2. The actual Gemini media input for `Q2` was:
   - `all_sample/sample_0/temp_clips/step_1_region_0.mp4`
   - absolute range: `0–240s`
   - stable preserved copy: `avp/out/q2_preserved_media/q2_opening_0_240_preserved.mp4`
3. The persisted observer evidence for `Q2` is:
   - `128–132s`
   - `155–159s`
   - `183–187s`
4. The dataset reference times extracted from `cot_reference` for `Q2` are:
   - `125s`
   - `157s`
   - `180s`
5. Therefore, `Q2` is an example of **semantic correctness with slight time-coordinate drift**, not total event mislocalization.
6. The remaining architectural gap is that region-mode inference still needs a stronger prompt-level rule that says:
   - the provided media may be a clip starting at local time `0`
   - all outputs must be converted back to canonical `raw_video_seconds`
   - for a clip covering `[A, B]`, absolute time = `A + local_clip_time`
7. `Q2` validation succeeded after this change:
   - run dir: `avp/out/q2_rerun_unified_20260321_162453`
   - answer still correct
   - planner / observer / synthesizer all operated on canonical raw-video seconds
8. `Q4` generalization validation exposed a real cross-question benefit:
   - run dir: `avp/out/q4_rerun_unified_20260321_162639`
   - initial planner incorrectly trusted `cot_reference` enough to choose `530–560s`
   - observer correctly rejected that region as not containing the target play
   - reflector marked evidence insufficient and triggered replanning instead of stopping early
   - replanning escalated to a uniform full-video scan, which then hit the yunwu endpoint payload limit (`413 Request Entity Too Large`)
9. Therefore, the remaining generalization blocker is no longer only time-base inconsistency; it is also the runtime's inability to perform a broad whole-video fallback efficiently on long videos.
10. `Q5` validation succeeded at the localization layer:
    - run dir: `avp/out/q5_rerun_unified_20260321_165250`
    - planner chose `[0,240]`
    - observer evidence: `102–104`, `127–130`, `156–159`, `184–187`
    - `cot_reference`: `104`, `125`, `157`, `187`
11. Therefore, the main cross-role time-base inconsistency is largely resolved on `Q5`; the remaining failure is that final synthesis answered with scoreboard elapsed time (`70s`) instead of canonical raw-video elapsed time (`~85s`).
12. A subsequent full `Q1~Q5` rerun at `avp/out/q1_to_q5_rerun_unified_20260321_170111` updated the picture:
    - `Q4` whole-video fallback succeeded with a re-encoded proxy clip and no longer failed with `413`
    - `Q5` answered `86s`, which is semantically within the reference range `75–90s`
13. This means the remaining issue is not a simple always-on deterministic `Q5` bug; model behavior is now variable enough that exact-match still underestimates the benefit of the time-base fix.
14. A fresh full `Q1~Q5` rerun at `avp/out/q1_to_q5_rerun_unified_20260321_180320` refined the picture again:
    - `Q2` stayed correct even though Planner drifted to `uniform full-video`
    - `Q4` again completed whole-video fallback, but the final answer now combined premise correction with a closest-scene counterfactual
    - `Q5` answered `83s`, matching the `cot_reference` chain exactly
15. Replacement-planning hardware audit for `Qwen3-Omni` found:
    - `2 x RTX 3090 (24 GiB each)`
    - `125 GiB` system RAM
    - `64` logical CPUs
    - about `321 GiB` free disk on `/`
16. Therefore, local deployment is possible only with a carefully chosen or quantized Qwen path; the machine should not be treated as if it can casually host the largest full-precision omni stack.
17. The current runtime replacement seam is concentrated in `GeminiClient` plus `eval_dataset.py`, which means AVP role orchestration can likely stay intact if the client layer becomes provider-agnostic.
18. The user has a `ModelScope` API token and wants a service-first validation path, so the first recommended milestone is no longer pure local deployment.
19. `ModelScope/Qwen3-Omni` appears to expose an OpenAI-compatible multimodal API, but the current `GEMINI_BASE_URL` hook is still inside the Google SDK path, so a dedicated provider implementation is required.
20. Recommended first replacement target:
    - model: `Qwen3-Omni-30B-A3B-Instruct`
    - serving path: `ModelScope API`
    - integration style: provider abstraction + explicit media adapter
    - follow-up A/B option: try `Qwen3-Omni-30B-A3B-Thinking` only for planner/synthesizer once the base path is stable

---

## Errors Encountered

| Error | Attempt | Resolution |
|-------|---------|------------|
| Sourced API key variables were not exported into subprocesses | 1 | Re-ran with `set -a` before `source api_key_config.txt` |
| `Controller.__init__()` missing `sample_metadata` argument | 1 | Added runtime wiring in `avp/main.py` |
| Cleanup raised `UnboundLocalError` when controller init failed | 1 | Guarded cleanup path in `avp/eval_dataset.py` |
| `session-catchup.py` invocation was blocked by shell-security because it used `$(pwd)` command substitution | 1 | Re-run the catchup script with an explicit absolute project path |

---

## Decisions Made

| Decision | Rationale |
|----------|-----------|
| Use `raw_video_seconds` as canonical internal reference | Reflector/synthesizer need one stable timeline |
| Treat `dataset_reference_only` as advisory, not authoritative | Current SportsTime references are not consistently trustworthy as direct raw-video coordinates |
| Preserve temp clips in debug runs | Needed to prove what media the Observer actually sent to Gemini |
| Fix prompt-level conversion, not just storage | The bug affects model reasoning, not only post-hoc analysis |

## Phase 8: Qwen3-Omni Provider Implementation (ModelScope API)

### Status: ✅ Code complete — awaiting end-to-end validation

### Completed Steps
1. ✅ Added provider config fields (`provider`, `modelscope_api_key`, `modelscope_base_url`, `modelscope_model`) to `AVPConfig`
2. ✅ Added `generate_text()` / `generate_with_media()` abstraction to `GeminiClient`
3. ✅ Refactored all 4 LLM call sites to use new methods
4. ✅ Fixed Reflector double-client anti-pattern (`self.client.client.models...`)
5. ✅ Created `ModelScopeQwenClient` in `avp/qwen_client.py`
6. ✅ Created `create_client()` factory function
7. ✅ Updated `eval_dataset.py` to use factory
8. ✅ Added `openai>=1.52.0` to requirements.txt
9. ✅ Verified imports, factory routing, and client initialization

### Remaining (Phase 4: Validation)
10. ⬜ Run Q2 with `provider: "modelscope_qwen"` — end-to-end validation
11. ⬜ Run Q5 with Qwen provider
12. ⬜ Compare Qwen vs Gemini results
