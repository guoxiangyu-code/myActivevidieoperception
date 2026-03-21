# Progress

## Session: 2026-03-21 — unified time-reference bugfix

### Completed so far

- Re-invoked the `planning-with-files` skill for the time-reference bugfix task.
- Recovered current repo state and reviewed existing planning files.
- Finished the partial runtime wiring that had previously broken `Controller(sample_metadata=...)`.
- Added runtime persistence for:
  - source video path
  - resolved video path
  - actual clip path sent to Gemini
  - absolute clip bounds
  - time-base metadata
- Re-ran `Q2` successfully with preserved temp clips:
  - output: `avp/out/q2_rerun_debug_20260321_153551`
  - final answer correct
- Re-ran `Q2` again after canonical-time enforcement:
  - output: `avp/out/q2_rerun_unified_20260321_162453`
  - final answer still correct
- Confirmed from persisted artifacts that `Q2` used a clipped `0–240s` media input.
- Copied the exact analysis clip to a stable preserved path:
  - `avp/out/q2_preserved_media/q2_opening_0_240_preserved.mp4`
  - SHA256: `d26555cc217a28eb44cfe8259269414f084a326d4b623b13c87dd8dd6d646172`
- Added formula-level local→global conversion instruction to Observer prompts and canonical-time guidance to Synthesizer prompts.
- Ran `Q4` as a generalization check:
  - output: `avp/out/q4_rerun_unified_20260321_162639`
  - initial region `530–560s` was rejected by Observer as the wrong play
  - Reflector triggered replanning correctly
  - replanned whole-video uniform fallback failed with `413 Request Entity Too Large`
- Re-ran `Q5` with the unified-time runtime:
  - output: `avp/out/q5_rerun_unified_20260321_165250`
  - planner used `[0,240]` and captured the full `8-0` sequence in one round
  - observer evidence: `102–104`, `127–130`, `156–159`, `184–187`
  - `cot_reference`: `104`, `125`, `157`, `187`
  - final answer was still wrong because synthesis used scoreboard elapsed time (`70s`) instead of raw-video elapsed time (`~85s`)
- Re-ran the full `Q1~Q5` set:
  - output: `avp/out/q1_to_q5_rerun_unified_20260321_170111`
  - strict exact-match remained `1/5`
  - `Q4` successfully used a re-encoded whole-video proxy clip and no longer failed with `413`
  - `Q5` answered `86秒`, which is semantically inside the reference range
- Regenerated the full Markdown analysis report:
  - `gemini_q1_to_q5_report.md`
- Re-ran the full `Q1~Q5` set again:
  - output: `avp/out/q1_to_q5_rerun_unified_20260321_180320`
  - strict exact-match stayed `1/5`
  - `Q2` stayed correct but Planner escalated to a full-video uniform scan
  - `Q4` again used whole-video fallback and returned a premise-corrected counterfactual answer
  - `Q5` answered `83秒`, matching the `cot_reference` chain exactly
- Refreshed the full Markdown analysis report again:
  - `gemini_q1_to_q5_report.md`
  - now reflects the `20260321_180320` artifact set rather than the earlier `170111` run

### Current focus

- Keep the latest full-run report and artifacts consistent with the current code state
- Distinguish stable `Q5` time-base gains from still-variable answer semantics and exact-match scoring limits
- Decide whether `Q2` planner drift toward whole-video scans should be constrained
- Treat `Q4` as an active dataset/source-video consistency question rather than only a search/fallback bug
- Ad hoc research request: compare current frontier models against `Gemini 2.5 Pro`, and determine whether any model is truly identical in handling style versus only operationally similar
- Preliminary model-research finding: no frontier model currently looks strictly identical to `Gemini 2.5 Pro`; the closest operational matches are likely `Gemini 2.5 Flash / Flash Live`, while OpenAI / Anthropic alternatives appear only partially similar
- Confirmed with official/public docs that Claude does not expose native video input like Gemini, and OpenAI's public video-analysis path is still materially different from Gemini's unified video-understanding workflow
- Open-model follow-up: the best current “Gemini-like” open candidate appears to be `Qwen3-Omni`, while `InternVideo 2.5 / Next` looks strongest when the comparison is narrowed specifically to video understanding
- Local replacement research conclusion: no single open local model clearly and stably exceeds `Gemini 2.5 Pro` overall; `Qwen3-Omni` is the best single-model substitute, while the highest-upside local path for AVP-style video QA is likely a hybrid stack built around `InternVideo 2.5 / Next` plus a strong open reasoning model
- New planning request in progress: inspect this machine's compute budget and design a concrete `Gemini -> Qwen3-Omni` replacement plan that matches the current AVP workflow
- First hardware/codebase scan completed:
  - machine has `2 x RTX 3090 24GB`
  - current Gemini-specific seam is mainly in `avp/config.py` and `avp/main.py`
  - repo already contains local Qwen runner prototypes in `avp/qwen_first_question_demo.py` and `avp/qwen_batch_eval.py`
- Hardware envelope clarified:
  - `Intel Xeon Silver 4314`, `64` logical CPUs
  - roughly `125 GiB` RAM with large swap available
  - about `321 GiB` free disk on `/`
  - conclusion so far: GPU VRAM, not RAM/CPU, is the primary sizing constraint for local `Qwen3-Omni`
- Qwen-side feasibility checkpoint:
  - existing prototype already demonstrates balanced multi-GPU local loading for `Qwen3.5`
  - however, the current local Qwen path is image-based rather than a true Gemini-style native video provider
  - planning assumption updated: `Qwen3-Omni` should be integrated behind a provider seam, with explicit video preprocessing owned by AVP
- Main integration seam narrowed further:
  - `GeminiClient` in `avp/main.py` is already the central dependency for planning, observation, reflection, and synthesis
  - migration effort is therefore concentrated in the client/provider layer, not across every agent role implementation
- Media-path audit clarified the main compatibility gap:
  - current Gemini runtime sends video/clip media directly to the provider
  - current local Qwen prototype samples frames locally with OpenCV and then reasons over images
  - the future plan therefore needs an explicit media-adapter layer, not only a model-name swap
- Scope has now been narrowed by user feedback:
  - initial replacement target is no longer pure local deployment
  - first milestone should use `ModelScope API` for `Qwen3-Omni` validation, with the existing local hardware audit kept as a later deployment reference
- Service-integration shape is now clearer:
  - `ModelScope/Qwen3-Omni` appears to expose an OpenAI-compatible multimodal API
  - existing `GEMINI_BASE_URL` support in AVP is not sufficient for this because the underlying SDK is still `google-genai`
  - recommended next step is a dedicated `ModelScopeQwenClient` provider, not a URL-only patch
- Dependency/wiring audit completed:
  - `requirements.txt` currently includes Gemini SDK and local-transformers stack, but not an OpenAI-compatible API client
  - `eval_dataset.py` still instantiates `GeminiClient` directly
  - conclusion: first implementation milestone needs provider selection + client factory wiring
- Planning phase completed:
  - recommended first model is `Qwen3-Omni-30B-A3B-Instruct`
  - recommended first serving mode is `ModelScope API`, not local deployment
  - recommended architecture is provider abstraction + explicit media adapter + staged validation on `Q2` then `Q5`
- New planning clarification captured:
  - "the underlying layer is `google-genai`" means the runtime is coupled to Google's Python SDK and `GeminiClient`, not only to a Gemini model name
  - consequence: ModelScope/Qwen integration needs a dedicated provider implementation instead of a `base_url`-only switch

### Error log

| Timestamp | Error | Attempt | Resolution |
|-----------|-------|---------|------------|
| 2026-03-21 | API key env not exported to subprocess | 1 | Re-ran with `set -a` before sourcing |
| 2026-03-21 | `Controller.__init__()` missing `sample_metadata` | 1 | Patched runtime wiring |
| 2026-03-21 | Cleanup crashed on missing `controller` | 1 | Guarded cleanup path |
| 2026-03-21 | Q5 final answer used scoreboard clock instead of raw-video elapsed time | 1 | Identified as a residual synthesis-level bug after timestamp alignment validation |
| 2026-03-21 | Q4 whole-video fallback previously hit `413 Request Entity Too Large` | 1 | Re-encoded full-video proxy clip path now runs successfully in the full 5-question evaluation |

### 5-Question Reboot Check

| Question | Answer |
|----------|--------|
| Where am I? | Post-reporting after the second full unified-time rerun |
| Where am I going? | Decide whether to stabilize Q2 planner drift, then continue on Q4 premise mismatch and Q1/Q3 attribution |
| What's the goal? | Preserve the gains from the canonical timeline fix and explain the remaining model/data inconsistencies clearly |
| What have I learned? | See `findings.md` |
| What have I done? | Re-ran Q1-Q5 twice, refreshed the report to the `18:03` artifact set, and recorded the new Q2/Q4/Q5 behaviors |

## Qwen Provider Abstraction Implementation (2025-06-21)

### Phase 1-3 Complete: Provider abstraction + ModelScope Qwen client

**Files modified:**
- `avp/config.py` — Added `provider`, `modelscope_api_key`, `modelscope_base_url`, `modelscope_model` fields with env overrides
- `avp/main.py` — Added `generate_text()` and `generate_with_media()` to `GeminiClient`; refactored all 4 LLM call sites; fixed Reflector double-client anti-pattern
- `avp/qwen_client.py` — **New file**: `ModelScopeQwenClient` (inherits GeminiClient, overrides LLM + media methods for DashScope API); `create_client()` factory
- `avp/eval_dataset.py` — Uses `create_client()` factory instead of direct `GeminiClient()` construction
- `requirements.txt` — Added `openai>=1.52.0`

**Key design decisions:**
1. Inheritance-based: `ModelScopeQwenClient(GeminiClient)` — reuses plan/observe/synthesize orchestration
2. Frame extraction for video: OpenCV-based, not Gemini-native video blob
3. Streaming API calls: DashScope Qwen-Omni requires `stream=True`
4. Factory resolves API key from env > config > modelscopeapi.txt file

**Verification:**
- Config fields load correctly with defaults
- Factory returns correct client types
- OpenAI client initializes with ModelScope API key
- No remaining direct `generate_content` calls outside base methods
- All imports pass without errors

**Next: Phase 4 — Run Q2 with Qwen provider to validate end-to-end**
