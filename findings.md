# Findings — unified time-reference investigation

## Core bug

The AVP runtime mixes multiple notions of time:

- dataset-side reference timestamps (`cot_reference`, `time_reference`)
- raw-video absolute seconds
- clip-local seconds when region mode sends a trimmed video file to Gemini

If these are not normalized into one canonical reference, different roles may reason over slightly different clocks and produce inference errors.

## Confirmed current behavior

### Q2 live rerun

- Run directory: `avp/out/q2_rerun_debug_20260321_153551`
- Source video path:
  - `dataset/Basketball/Full/Basketball_Full_001/Basketball_Full_001_1.mp4`
- Actual Gemini media input:
  - `all_sample/sample_0/temp_clips/step_1_region_0.mp4`
- Stable preserved copy:
  - `avp/out/q2_preserved_media/q2_opening_0_240_preserved.mp4`
- SHA256:
  - `d26555cc217a28eb44cfe8259269414f084a326d4b623b13c87dd8dd6d646172`
- Absolute clip bounds:
  - `0–240s`

### Q2 observed evidence

- Observer evidence (`evidence/round_1/evidence.json`):
  - `128–132s` → Thunder 2-0
  - `155–159s` → Thunder 5-0, first 3PT
  - `183–187s` → Thunder 8-0, second 3PT
- Dataset reference times (`meta.json` / `sample_metadata.json`):
  - `125s`, `157s`, `180s`

### Interpretation

- `Q2` answer remains correct because the question is about **counting** (`2次`) rather than exact timestamp reporting.
- The mismatch is small enough that the semantic event sequence is still correct.
- The remaining problem is **coordinate consistency**, not inability to recognize the event.

## Important architectural finding

The Observer in region mode often sends a **clipped video file** to Gemini.

That means:
- the media itself is naturally in `clip_local_seconds`
- but the framework expects the model to output `raw_video_seconds`

The prompt already says “use original video timestamps”, but it does **not yet state the conversion formula explicitly enough**:

- for a clip covering `[A, B]`,
- local clip time `t_local`
- should be reported as absolute time `A + t_local`

This is the most direct remaining fix for unified time-reference reasoning.

## Why timing inconsistencies occur

There are three distinct sources of inconsistency:

1. **Dataset reference clock vs raw-video clock**
   - SportsTime `cot_reference` is not guaranteed to be the same as raw-video absolute seconds.
   - In `Q2`, the mismatch is small (`125/157/180` vs `128-132/155-159/183-187`), but it still shows the two clocks are not perfectly identical.

2. **Clip-local clock vs raw-video clock**
   - Region mode frequently sends a trimmed clip to Gemini.
   - The clip naturally starts at local time `0.0s`, but AVP expects output in original raw-video seconds.
   - If the local→global mapping is not made explicit enough, the model can mix the two references.

3. **Event-boundary choice**
   - Annotation may refer to one anchor (e.g. scoreboard update or event completion),
   - while Observer may report a slightly wider interval around the visible action.
   - After full-second normalization (`floor/ceil`), a small visual difference becomes a 2–4 second interval shift.

## Runtime improvements already landed

- Per-sample time-base metadata is now persisted into run artifacts.
- Planner and Observer prompts now include temporal-context disclaimers.
- Observer prompts now include explicit clip-local → original-video conversion rules.
- `Controller` now receives sample metadata.
- `conversation_history.json` now stores runtime metadata and `model_call`.
- `model_call` now stores media-input and time-normalization metadata.
- Observer duplicate evidence insertion was removed.
- Reflector now blocks early success when evidence explicitly says the decisive event is outside the watched window.

## Remaining validation target

### Q2 verification

- Unified rerun output: `avp/out/q2_rerun_unified_20260321_162453`
- Result: still correct
- Meaning: canonical-time unification did not regress a previously working count question.

### Q4 generalization result

- Unified rerun output: `avp/out/q4_rerun_unified_20260321_162639`
- Initial planner chose `530–560s` based on dataset-local reference hints.
- Observer correctly found that this clip does **not** contain the target event.
- Reflector correctly rejected the evidence and forced replanning.
- Replanner then switched to a full-video uniform scan, which failed with:
  - `413 Request Entity Too Large`

### Updated conclusion

The time-reference fix is directionally correct and already helps across questions:
- wrong reference-based region assumptions are now more visible,
- wrong clips no longer lead to premature stopping.

However, full generalization now depends on a second engineering fix:
- broad fallback search on long videos must avoid sending the entire original file inline to the API.
- likely remedies include compressed/re-encoded fallback media or chunked coarse search.

## Q5 live validation

- Unified rerun output: `avp/out/q5_rerun_unified_20260321_165250`
- Source video path:
  - `dataset/Basketball/Full/Basketball_Full_001/Basketball_Full_001_1.mp4`
- Actual Gemini media input:
  - `all_sample/sample_0/temp_clips/step_1_region_0_0_240000.mp4`
- Absolute clip bounds:
  - `0–240s`

### Q5 observed evidence vs `cot_reference`

- Dataset reference times (`cot_reference`):
  - `104s` → jump ball / 0-0
  - `125s` → 2-0
  - `157s` → 5-0
  - `187s` → 8-0
- Observer evidence (`evidence/round_1/evidence.json`):
  - `102–104s` → jump ball / 0-0
  - `127–130s` → 2-0
  - `156–159s` → 5-0
  - `184–187s` → 8-0

### Interpretation

- The event chain is now aligned to one canonical raw-video timeline and matches the `cot_reference` order exactly.
- The remaining drift is small:
  - jump ball: `-2` to `0` seconds
  - first basket: `+2` to `+5` seconds
  - second basket: `-1` to `+2` seconds
  - third basket / `8-0`: `-3` to `0` seconds
- This is consistent with normal event-boundary and full-second interval rounding, not the earlier severe time-base mismatch.

### Residual bug revealed by Q5

- The final answer is still wrong, but for a different reason than before.
- Canonical raw-video evidence gives an elapsed duration of about `85s` (`187 - 102`), which falls inside the ground-truth range `75–90s`.
- The synthesizer instead used the on-screen scoreboard clock:
  - `12:00 -> 10:50 = 70s`
- Therefore, the remaining issue is **duration semantics**, not localization:
  - AVP now localizes the events on a consistent raw-video timeline,
  - but the final answer still prefers scoreboard elapsed time over raw-video elapsed time for this question.

## Updated conclusion

The Q5 rerun shows that the cross-role timestamp inconsistency has been substantially reduced: planner, observer, verifier, and synthesizer all share the same canonical event anchors (`102/127/156/184`-style raw-video timestamps).

The next fix should target answer generation for duration questions: unless the query explicitly asks for game-clock time, duration should be computed from canonical raw-video timestamps rather than from the scoreboard clock visible in the frame.

## Full Q1-Q5 rerun after fallback changes

- Full rerun output: `avp/out/q1_to_q5_rerun_unified_20260321_170111`
- Updated report: `gemini_q1_to_q5_report.md`

### What changed in Q4

- The runtime now successfully generated and used a re-encoded full-video proxy clip:
  - `all_sample/sample_3/temp_clips/step_1_uniform_reencoded_0_1839367.mp4`
  - size: about `47.78 MB`
- This avoided the earlier `413 Request Entity Too Large` blocker.
- After the fallback completed, the model concluded that the question premise does not match the raw source video:
  - second quarter starts around `1258–1261s`
  - Speights enters only around `1698–1700s`
  - the closest layup occurs around `1808–1812s`, but the primary defender is Barnes, not Speights

### What changed in Q5

- In the full five-question rerun, `Q5` answered:
  - `86 秒（约1分26秒）`
- Supporting evidence:
  - `102–104s` jump ball
  - `128–132s` → `2-0`
  - `155–159s` → `5-0`
  - `184–188s` → `8-0`
- This answer is semantically within the reference range `75–90s`.

### Revised interpretation

- The canonical-time fix clearly improved `Q5`, but the answer behavior is not fully stable:
  - isolated `Q5` rerun previously answered with scoreboard elapsed time (`70s`)
  - full `Q1~Q5` rerun answered with raw-video elapsed time (`86s`)
- Therefore, the remaining issue is better described as **answer-semantics instability** rather than a guaranteed deterministic localization bug.

## Fresh Q1-Q5 rerun at `20260321_180320`

- Full rerun output: `avp/out/q1_to_q5_rerun_unified_20260321_180320`
- Updated report: `gemini_q1_to_q5_report.md`

### What changed in Q2

- `Q2` stayed correct, but Planner no longer selected a short opening region.
- Instead, it chose `uniform full-video` and used a re-encoded whole-video proxy clip.
- This means correctness is stable, but planning cost is now more variable than before.

### What changed in Q4

- The whole-video fallback remained stable and again located the closest real play near `1810–1813s`.
- This time the final answer did not stop at "the premise is inaccurate".
- Instead, it used the corrected scene interpretation to answer the counterfactual: even if Speights simply held position instead of jumping to contest, Westbrook would still likely finish the layup.

### What changed in Q5

- `Q5` answered:
  - `83 秒`
- Supporting evidence:
  - `103–187s` full scoring-run span
  - `129–131s` → `2-0`
  - `156–158s` → `5-0`
  - `185–187s` → `8-0`
- This matches the `cot_reference` chain `01:44 -> 03:07 = 83s` exactly.

### Revised interpretation

- `Q5` now has a fresh rerun that lands exactly on the reference elapsed time, so the time-base fix is clearly doing real work.
- However, the contrast across `70s` -> `86s` -> `83s` answers still indicates that answer style and planning behavior can vary between runs.
- `Q2` also shows planner-policy drift: a question that can be solved from the opening clip is now sometimes routed through a whole-video scan.

## Ad hoc model-landscape research: Gemini 2.5 Pro peers

### Preliminary conclusion

- There is no clear evidence that any current frontier model is **completely identical** to `Gemini 2.5 Pro` in handling style.
- The closest operational match is likely **within Google's own family**, especially `Gemini 2.5 Flash` / `Gemini 2.5 Flash Live`, because they share the same model family, multimodal API surface, and native video-oriented workflow style.
- Competing frontier models from OpenAI and Anthropic may match Gemini on some dimensions (reasoning, tool use, structured output), but they do not appear to match the full combination of:
  - native long-video understanding
  - Gemini-style multimodal ingestion
  - very long context
  - same-family API / runtime behavior

### Why this matters for this repo

- For AVP-style workloads, the most relevant question is not “which model is strongest overall,” but “which one behaves most similarly when asked to ingest long multimodal evidence and return structured reasoning.”
- On that narrower engineering question, the early signal is that **Gemini-family substitutes** are much closer than cross-vendor substitutes.

### Confirmed differences versus Anthropic and OpenAI

- **Anthropic Claude**:
  - Official public documentation supports text and image workflows, but does **not** expose native video input in the same way Gemini does.
  - In practice, Claude-style video workflows are closer to “extract frames, then send images” than to Gemini’s native video ingestion.

- **OpenAI GPT / Sora**:
  - Public OpenAI materials emphasize vision over images, cookbook-style frame extraction for video understanding, and `Sora` for video generation.
  - That is not the same operational path as Gemini 2.5 Pro’s native video analysis interface.

### Interim conclusion

- If the question is “which model has the **same cross-vendor handling path** as Gemini 2.5 Pro?”, the current evidence still points to **none**.
- If the question is “which model is the **closest practical substitute**?”, the answer still points first to **Gemini 2.5 Flash / Flash Live**, not to Claude or OpenAI GPT models.

### Open-model-specific conclusion

- If the question is narrowed to **open-weight / open-source models**, the strongest current candidate for “most Gemini-like” behavior appears to be **Qwen3-Omni**.
- Reason:
  - it is built for native end-to-end multimodality rather than only image vision
  - it explicitly targets text / image / audio / video handling in one model family
  - it is closer to Gemini’s “single multimodal agent model” shape than Claude, GPT, or video-only research stacks

- A second important open candidate is **InternVideo / InternVideo-Next**:
  - it is especially strong on native video understanding
  - but it is more naturally viewed as a **video-first multimodal stack** than as a full Gemini-style general-purpose agent model

- Therefore, for open models:
  - **closest all-around Gemini-like candidate**: `Qwen3-Omni`
  - **closest video-specialist line**: `InternVideo 2.5 / Next`
  - **strictly equal**: still **none**

## Open local replacements for Gemini 2.5 Pro

### High-confidence conclusion

- There is still **no single open local model** that can be claimed to stably and comprehensively **flat-replace or exceed** `Gemini 2.5 Pro` across:
  - native multimodal video understanding
  - long-context reasoning
  - structured/agent-style output
  - production-level robustness

- The best current open local substitutes fall into two categories:
  1. **single-model replacement candidates**
  2. **video-specialist + reasoning-model combinations**

### Best single-model open local replacement

- **`Qwen3-Omni`**
  - strongest candidate for an all-around local open replacement
  - closest to Gemini’s “one model handles text/image/audio/video” shape
  - strongest when the requirement is a unified multimodal assistant rather than a video-only stack
  - can beat Gemini-class systems on some audio/audio-visual sub-benchmarks, but not clearly across the board

### Best video-specialist replacement line

- **`InternVideo 2.5 / InternVideo-Next`**
  - strongest open line when the comparison is narrowed specifically to **video understanding**
  - especially strong on long-video, temporal grounding, and perception-heavy benchmarks
  - but less naturally a full Gemini-style general-purpose agent model by itself

### Best mature balanced alternatives

- **`Qwen2.5-VL` / `Qwen2.5-Omni`**
  - more mature and easier to integrate than some research-first video stacks
  - strong practical choice for local multimodal deployment, especially for Chinese + vision/video workflows

- **`InternVL` family**
  - strong balanced multimodal open models
  - attractive when video is important but not the only modality

### Practical replacement ranking for this repo style

For AVP-style video QA / agentic reasoning, the current ranking is:

1. **Best single-model local replacement**: `Qwen3-Omni`
2. **Best video-first open stack**: `InternVideo 2.5 / Next` + a strong open reasoning LLM
3. **Best balanced mature local backup**: `Qwen2.5-VL` or `Qwen2.5-Omni`
4. **Best Chinese-friendly multimodal backup**: `InternVL`

### Most important engineering insight

- If the goal is to **match or exceed Gemini on this repo’s workload**, the highest-upside path is probably **not** a single open model.
- The stronger path is a **hybrid local stack**:
  - video-specialist model for perception/localization
  - strong open reasoning model for planning/synthesis
  - explicit structured-output prompting / tool-use orchestration

- In other words:
  - **single-model simplicity** -> `Qwen3-Omni`
  - **highest ceiling on video tasks** -> `InternVideo`-style perception + open reasoning stack

## Kickoff for local Qwen3-Omni replacement planning

- New task focus:
  - audit the current machine's available compute
  - map the existing Gemini integration seam in AVP
  - choose a realistically deployable `Qwen3-Omni` variant rather than assuming the largest checkpoint fits
- Planning constraint:
  - the replacement target is not generic "multimodal chat", but the current AVP plan -> observe -> reflect -> synthesize workflow that consumes video and expects structured JSON outputs
- Immediate next checks:
  - `nvidia-smi`, `lscpu`, `free -h`, and storage availability
  - Gemini- and Qwen-related runtime/config entry points under `avp/`

### First concrete findings

- Local GPU inventory:
  - `2 x NVIDIA GeForce RTX 3090`
  - VRAM per GPU: `24 GiB`
  - both cards were almost fully free during inspection
- This means the machine is suitable for:
  - a compact or quantized `Qwen3-Omni` deployment
  - a multi-GPU split deployment if the backend supports it
- This machine is **not** a safe assumption for blindly loading the largest unquantized omni checkpoint end-to-end.
- Existing replacement-friendly code seam already exists in the repo:
  - `avp/config.py` centralizes model/config fields and currently defaults to Gemini-family names plus Gemini env vars
  - `avp/main.py` imports `google.genai` directly and holds the current Gemini client path
  - `avp/qwen_first_question_demo.py` and `avp/qwen_batch_eval.py` already provide a local in-process Qwen runner pattern that can be reused conceptually for a production replacement
- Immediate implication:
  - the cleanest migration path is likely **not** to rewrite AVP from scratch
  - instead, introduce a provider seam so the existing Gemini path and local Qwen path can share the same planning / observation / reflection orchestration

### Hardware envelope in more detail

- CPU:
  - `Intel Xeon Silver 4314 @ 2.40GHz`
  - `64` logical CPUs
- RAM / swap:
  - system RAM: about `125 GiB`
  - available RAM at inspection time: about `114 GiB`
  - swap: about `255 GiB`, with substantial headroom still available
- Storage:
  - root filesystem free space: about `321 GiB`
- Operational implication:
  - CPU, RAM, and swap are generous enough to support heavy preprocessing and some offload
  - the **real bottleneck is still GPU VRAM** (`24 GiB` per card), so model selection must be VRAM-first
  - disk space is usable but not abundant enough to casually mirror many very large checkpoints plus extracted video caches

### Current Gemini integration seam

- `AVPConfig` already separates:
  - `plan_replan_model`
  - `execute_model`
- This is good news for a migration because the codebase already has the concept of:
  - a planner/synthesizer model
  - a separate execution / observation model
- The current blockers are provider-specific rather than architecture-specific:
  - env/config fields are named around Gemini (`GEMINI_MODEL`, `GEMINI_API_KEY`, `GEMINI_BASE_URL`)
  - `avp/main.py` imports and instantiates the Google client directly instead of going through a provider interface

### Qwen-side prototype reality check

- The existing local Qwen prototype in `avp/qwen_first_question_demo.py`:
  - loads `Qwen3.5` via `transformers`
  - uses `device_map="balanced"` when multiple GPUs are available
  - caps each GPU at about `18 GiB` and allows CPU spillover
- This is useful as engineering prior art because it shows:
  - this machine can already support a split-GPU local multimodal runner pattern
  - the repo already has some model-caching and local-runner logic worth reusing
- But it is **not** a drop-in Gemini replacement yet:
  - it is a `Qwen3.5` prototype, not `Qwen3-Omni`
  - the input preparation path is image-oriented (`images + text`), so replacing Gemini's native video path will still require a video-to-frame or video-aware provider layer

### Qwen3-Omni sizing takeaway

- Public official/open materials indicate that the key open `Qwen3-Omni` family checkpoint is:
  - `Qwen3-Omni-30B-A3B`
  - with open variants such as `Instruct`, `Thinking`, and `Captioner`
- Practical deployment implication on this machine:
  - full-precision deployment is not the right assumption
  - a quantized deployment and/or component-reduced deployment is the realistic target
  - `2 x 3090 24GB` is in the plausible zone for a carefully configured local run, but not for a careless full-stack omni deployment with maximal memory settings
- Therefore the recommended direction is:
  - treat `Qwen3-Omni` as a **served local backend**
  - keep AVP orchestration outside the model
  - explicitly control video preprocessing and structured-output prompting in the application layer

### More precise replacement seam in `avp/main.py`

- The current runtime already concentrates most provider-specific behavior inside `GeminiClient`.
- High-value properties of the current design:
  - planning uses `plan_replan_model`
  - observation uses `execute_model`
  - final synthesis also goes through the same client object
- `Planner`, `Observer`, `Reflector`, and `Controller` all depend on that client object rather than constructing Google SDK clients themselves.
- This is a strong migration advantage because the clean replacement path is:
  1. define a provider-agnostic client interface
  2. keep the orchestration roles unchanged
  3. swap `GeminiClient` for a local `QwenOmniClient` or a more generic `ModelClient`
- In other words, the codebase does **not** require a role-by-role rewrite; the main work is concentrated in:
  - client abstraction
  - media input translation
  - structured output / prompt-compatibility guards

### Media-path mismatch that the replacement must solve

- The current Gemini observation path in `avp/main.py`:
  - builds actual video media parts / clip parts
  - sends them through `self.client.models.generate_content(...)`
  - therefore relies on the provider to understand video directly
- The existing local Qwen prototype does something materially different:
  - opens the video locally
  - samples frames with OpenCV
  - saves frame previews / frame metadata
  - builds observer chunks from `sampled_images` and `sampled_meta`
- Therefore a `Gemini -> Qwen3-Omni` migration is not just:
  - changing a config string
  - or swapping one SDK call for another
- The replacement must include a **media adapter** that decides one of these paths:
  1. native Qwen3-Omni video input, if the chosen backend truly supports it locally and robustly
  2. explicit AVP-owned frame sampling / chunking, with prompt instructions that preserve the canonical raw-video timeline

### Why the second path is safer

- AVP already has delicate timestamp logic and canonical time-base fixes.
- If the application owns frame sampling and timestamp metadata explicitly:
  - debugging stays transparent
  - role traces stay reconstructible
  - cross-role timestamp normalization remains under our control instead of being hidden inside a black-box serving stack

### Scope update from user: service-first, not local-first

- The user clarified that they already have a `ModelScope` API token locally and want to:
  - use `modelscope.cn` service first
  - avoid full local deployment for the initial replacement test
- This changes the recommended implementation order:
  1. first build a `Qwen3-Omni via ModelScope API` provider path
  2. validate AVP behavior and JSON/prompt compatibility
  3. only then decide whether local deployment is worth the extra engineering complexity
- Security note:
  - token material lives in a local file and should be consumed as a secret input only
  - the plan should avoid copying the token into config files committed to the repo

### ModelScope API implications

- Public documentation indicates that Qwen3-Omni service access is exposed through an **OpenAI-compatible chat/completions style API** with multimodal support.
- This is strategically helpful because:
  - Python client integration can be done with the standard `openai` SDK or plain HTTP
  - the service-first validation path becomes much lighter than full local deployment
- But there is an important non-obvious constraint:
  - the current AVP runtime's `base_url` knob lives inside the **Google Gemini SDK path**
  - therefore `GEMINI_BASE_URL` is **not** enough to turn the current `GeminiClient` into a ModelScope/Qwen client by itself
- Practical consequence:
  - the migration still needs a new provider implementation
  - however, that provider can be much thinner than a fully local inference backend because the remote service handles model hosting

### Updated recommendation

- First replacement milestone should be:
  - keep the AVP orchestration
  - add a provider abstraction
  - implement a `ModelScopeQwenClient` against an OpenAI-compatible API
  - validate prompts, JSON parsing, and video/media handling on a small sample set
- Only after that should the project decide whether to:
  - continue with hosted Qwen service
  - or invest in local deployment / quantization work

### Dependency and wiring implications

- Current Python dependencies already cover:
  - `google-genai`
  - local `transformers` / `accelerate` / `torch`
- But they do **not** currently include an OpenAI-compatible client dependency.
- `eval_dataset.py` currently instantiates `GeminiClient` directly, so the batch runner is not provider-agnostic yet.
- This means the first real integration step will require:
  - a provider selection mechanism in config
  - a provider factory in the runner path
  - likely adding `openai` (or an explicit HTTP client path) to `requirements.txt`
- Engineering implication:
  - the hosted `ModelScope` route is still much simpler than full local deployment
  - but it remains a **code change**, not just an environment-variable change

### Final recommended design

- **Recommended first model**:
  - `Qwen3-Omni-30B-A3B-Instruct`
- **Why not start with `Thinking`**:
  - first milestone should optimize for stable JSON/structured outputs, lower latency, and easier debugging
  - `Thinking` is better treated as a controlled follow-up experiment for planner/synthesizer quality, not the initial baseline
- **Recommended first architecture**:
  1. add `provider: gemini | modelscope_qwen` to config
  2. introduce a provider-agnostic client interface
  3. keep existing AVP roles and controller flow unchanged
  4. implement `ModelScopeQwenClient` using the hosted API and a local secret file / env export for the token
  5. add a media-adapter layer for observer inputs
  6. first prefer explicit AVP-owned frame sampling for Qwen observation so canonical raw-video time remains transparent and debuggable
  7. preserve the existing role trace / evidence persistence contract so new runs stay comparable to Gemini runs
- **Recommended rollout**:
  - Step 1: make planner + reflector + synthesizer provider-agnostic and working with hosted Qwen
  - Step 2: port observer through the media adapter using explicit frame/time metadata
  - Step 3: run a very small sample check (`Q2`, then `Q5`)
  - Step 4: compare answer correctness, role-trace completeness, latency, and timestamp consistency against the current Gemini baseline
  - Step 5: only if worthwhile, test `Thinking` for planning/synthesis or direct-video service mode as a second-stage experiment

## Meaning of "the underlying layer is google-genai"

- In this repo, that phrase does **not** just mean "the current model is Gemini."
- It means the runtime is structurally coupled to the **Google Gen AI Python SDK**:
  - imports use `from google import genai`
  - the main runtime client is `GeminiClient`
  - real calls are made through `genai.Client(...).models.generate_content(...)`
- So the current architecture is not merely:
  - `model = gemini-2.5-pro`
- It is also:
  - Google SDK request/response types
  - Google SDK client initialization
  - Google-style media payload handling
- Therefore, replacing Gemini with `ModelScope/Qwen3-Omni` requires a provider-layer change rather than a pure config string change.
