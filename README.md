# ActiveVideoPerception: Our Long-Video QA Demo on SportsTime

<div align="center">

[![Demo Report](https://img.shields.io/badge/Demo%20Report-5%20Questions-4F46E5)](gemini_q1_to_q5_report.md)
[![Manual Review](https://img.shields.io/badge/Manual%20Review-4%2F5%20Correct-16A34A)](#five-question-demo-showcase)
[![Model](https://img.shields.io/badge/Model-Gemini%202.5%20Pro-0EA5E9)](gemini_q1_to_q5_report.md)
[![Dataset](https://img.shields.io/badge/Dataset-SportsTime-F59E0B)](gemini_q1_to_q5_report.md)
[![Framework](https://img.shields.io/badge/Framework-AVP-DC2626)](https://github.com/SalesforceAIResearch/ActiveVideoPerception)

</div>

<div align="center">
  <img src="assets/teaser.png" width="760" alt="Active Video Perception teaser">
</div>

> A demo-oriented engineering fork of **Active Video Perception (AVP)** for long basketball video question answering.

**Fork authors & README authors:** Guoxiangyu × GitHub Copilot  
**Based on:** the original [Active Video Perception](https://github.com/SalesforceAIResearch/ActiveVideoPerception) project and paper by Salesforce AI Research & collaborators  
**Current demo model:** `gemini-2.5-pro` (via `yunwu.ai`)  
**Current demo video:** `Basketball_Full_001_1.mp4` from the SportsTime basketball set

---

## What this repository is about

This repository focuses on making AVP **observable, reproducible, and practical** for long-form sports video reasoning.

Compared with a plain one-shot video QA pipeline, AVP decomposes the task into four agent roles:

1. **Planner** — decides what part of the video to inspect.
2. **Observer** — watches clips or full-video fallbacks and extracts evidence.
3. **Reflector / Verifier** — judges whether the current evidence is sufficient.
4. **Synthesizer** — writes the final answer from the accumulated evidence.

In our fork, we especially emphasize:

- unified time references across roles,
- preserved intermediate evidence,
- role-level traces for debugging,
- stable long-video fallback behavior,
- and demo-friendly outputs that can be inspected sample by sample.

---

## Demo snapshot

The raw evaluator in `summary.json` uses **strict exact-match**, so it reports only `1/5`.

After reading the full answer texts and evidence chains in `gemini_q1_to_q5_report.md`, **our manual review conclusion** is:

| View | Result | Interpretation |
|------|--------|----------------|
| Raw exact-match metric | `1 / 5` | Too strict for open-ended answers |
| Our manual semantic review | `4 / 5` | `Q1`, `Q2`, `Q3`, and `Q5` are substantively correct |
| Remaining unresolved item | `Q4` | The question premise does not cleanly match the source video |

**Primary demo artifacts**

- Full report: [`gemini_q1_to_q5_report.md`](gemini_q1_to_q5_report.md)
- Full run directory: [`avp/out/q1_to_q5_rerun_unified_20260321_180320/`](avp/out/q1_to_q5_rerun_unified_20260321_180320/)
- Per-question artifacts: `all_sample/sample_0` through `all_sample/sample_4`

---

## Five-question demo showcase

| Q | Task Type | Our Verdict | Why we count it this way | Demo Artifacts |
|---|-----------|-------------|---------------------------|----------------|
| **Q1** | Causal reasoning | ✅ Correct | The model explicitly captures the key cause chain: Curry is blocked by the screen and fouls while recovering. | [`sample_0`](avp/out/q1_to_q5_rerun_unified_20260321_180320/all_sample/sample_0/) |
| **Q2** | Perception / counting | ✅ Correct | It correctly counts the two OKC three-point attempts before the score reaches 8. | [`sample_1`](avp/out/q1_to_q5_rerun_unified_20260321_180320/all_sample/sample_1/) |
| **Q3** | Tactical reasoning | ✅ Correct | The answer includes the decisive dribble-created shot space; the extra “switch mismatch” context does not overturn the core conclusion. | [`sample_2`](avp/out/q1_to_q5_rerun_unified_20260321_180320/all_sample/sample_2/) |
| **Q4** | Counterfactual reasoning | ❌ Incorrect / unresolved | The system finds the nearest real play, but the question premise itself does not align cleanly with the video. | [`sample_3`](avp/out/q1_to_q5_rerun_unified_20260321_180320/all_sample/sample_3/) |
| **Q5** | Temporal reasoning | ✅ Correct | The model answers `83 秒`, which matches the evidence chain exactly and lies inside the reference range `75–90 秒`. | [`sample_4`](avp/out/q1_to_q5_rerun_unified_20260321_180320/all_sample/sample_4/) |

### Q1 — Causal reasoning

<details>
<summary><strong>Open details</strong></summary>

**Question**  
比赛刚开始，金州勇士队30号球员（斯蒂芬·库里）对俄克拉荷马城雷霆队35号球员（凯文·杜兰特）犯规，最直接的原因是什么？

**Reference answer**  
`金州勇士队30号球员在防守掩护时被挡住`

**Model answer**  
The answer says Curry is blocked while chasing Durant off a screen, then reaches and fouls to stop the play.

**Why we count it as correct**  
The model recovers the crucial causal structure — **Curry is screened off while defending** — and then gives an extra layer of detail about the foul itself. Under exact-match it is marked wrong, but under semantic review it captures the intended basketball cause.

**Artifacts**

- [`plan.initial.json`](avp/out/q1_to_q5_rerun_unified_20260321_180320/all_sample/sample_0/plan.initial.json)
- [`evidence.json`](avp/out/q1_to_q5_rerun_unified_20260321_180320/all_sample/sample_0/evidence/round_1/evidence.json)
- [`final_answer.json`](avp/out/q1_to_q5_rerun_unified_20260321_180320/all_sample/sample_0/final_answer.json)
- [`role_traces.jsonl`](avp/out/q1_to_q5_rerun_unified_20260321_180320/all_sample/sample_0/role_traces.jsonl)

</details>

### Q2 — Perception / counting

<details>
<summary><strong>Open details</strong></summary>

**Question**  
比赛开始后，俄克拉荷马城雷霆队在得到前8分的过程中，总共完成了多少次三分出手？

**Reference answer**  
`2次`

**Model answer**  
The model answers that OKC took **2 three-point attempts** while scoring its first 8 points.

**Why we count it as correct**  
This is the cleanest demo sample: the answer, evidence, and timestamps all align.

**Artifacts**

- [`plan.initial.json`](avp/out/q1_to_q5_rerun_unified_20260321_180320/all_sample/sample_1/plan.initial.json)
- [`evidence.json`](avp/out/q1_to_q5_rerun_unified_20260321_180320/all_sample/sample_1/evidence/round_1/evidence.json)
- [`final_answer.json`](avp/out/q1_to_q5_rerun_unified_20260321_180320/all_sample/sample_1/final_answer.json)
- [`role_traces.jsonl`](avp/out/q1_to_q5_rerun_unified_20260321_180320/all_sample/sample_1/role_traces.jsonl)

</details>

### Q3 — Tactical reasoning

<details>
<summary><strong>Open details</strong></summary>

**Question**  
金州勇士队30号球员在比赛开局阶段命中三分球，这个得分机会主要是通过哪种方式创造出来的？

**Reference answer**  
`利用个人运球技巧创造出投篮空间`

**Model answer**  
The model explains that Curry creates space with individual dribble moves, while also mentioning the switch mismatch as surrounding context.

**Why we count it as correct**  
Our manual judgment focuses on the **main mechanism** of the scoring chance. The answer explicitly names Curry's dribble creation and shot-space generation, which is the key substance of the reference.

**Artifacts**

- [`plan.initial.json`](avp/out/q1_to_q5_rerun_unified_20260321_180320/all_sample/sample_2/plan.initial.json)
- [`evidence.json`](avp/out/q1_to_q5_rerun_unified_20260321_180320/all_sample/sample_2/evidence/round_1/evidence.json)
- [`final_answer.json`](avp/out/q1_to_q5_rerun_unified_20260321_180320/all_sample/sample_2/final_answer.json)
- [`role_traces.jsonl`](avp/out/q1_to_q5_rerun_unified_20260321_180320/all_sample/sample_2/role_traces.jsonl)

</details>

### Q4 — Counterfactual reasoning

<details>
<summary><strong>Open details</strong></summary>

**Question**  
在第二节初期，俄克拉荷马城雷霆队0号球员（拉塞尔·威斯布鲁克）成功突破金州勇士队5号球员（马里斯·斯贝茨）的防守并上篮得分。如果当时勇士队5号球员在防守时没有试图去“抢断”，而是选择“保持防守位置”，那么最可能发生什么？

**Reference answer**  
`雷霆队0号球员的进攻会受到更大的干扰，可能无法轻松得分`

**Model answer**  
The model first corrects the scene premise, then answers a nearby counterfactual on the closest real play it can locate.

**Why this remains unresolved**  
The system is no longer failing because it cannot search long videos. Instead, it is hitting a harder issue: **the prompt premise and the source video do not line up cleanly**. This is the only demo item we do not count as correct.

**Artifacts**

- [`plan.initial.json`](avp/out/q1_to_q5_rerun_unified_20260321_180320/all_sample/sample_3/plan.initial.json)
- [`round_1 evidence`](avp/out/q1_to_q5_rerun_unified_20260321_180320/all_sample/sample_3/evidence/round_1/evidence.json)
- [`round_2 evidence`](avp/out/q1_to_q5_rerun_unified_20260321_180320/all_sample/sample_3/evidence/round_2/evidence.json)
- [`final_answer.json`](avp/out/q1_to_q5_rerun_unified_20260321_180320/all_sample/sample_3/final_answer.json)
- [`role_traces.jsonl`](avp/out/q1_to_q5_rerun_unified_20260321_180320/all_sample/sample_3/role_traces.jsonl)

</details>

### Q5 — Temporal reasoning

<details>
<summary><strong>Open details</strong></summary>

**Question**  
比赛开局时，俄克拉荷马城雷霆队以8-0领先金州勇士队。在这波进攻高潮中，雷霆队大概花了多少时间才取得这8分？

**Reference answer**  
`约75秒到90秒之间`

**Model answer**  
`83 秒`

**Why we count it as correct**  
`83 秒` is fully consistent with the evidence chain and falls inside the reference range `75–90 秒`. This is a classic case where exact-match underestimates an open-ended answer.

**Artifacts**

- [`plan.initial.json`](avp/out/q1_to_q5_rerun_unified_20260321_180320/all_sample/sample_4/plan.initial.json)
- [`evidence.json`](avp/out/q1_to_q5_rerun_unified_20260321_180320/all_sample/sample_4/evidence/round_1/evidence.json)
- [`final_answer.json`](avp/out/q1_to_q5_rerun_unified_20260321_180320/all_sample/sample_4/final_answer.json)
- [`role_traces.jsonl`](avp/out/q1_to_q5_rerun_unified_20260321_180320/all_sample/sample_4/role_traces.jsonl)

</details>

---

## How the AVP loop works

<div align="center">
  <img src="assets/vis.png" width="900" alt="AVP visualization">
</div>

AVP treats long-video understanding as an **iterative evidence-seeking process** rather than a single monolithic forward pass.

```text
Planner -> Observer -> Reflector / Verifier -> Synthesizer
   ^                                              |
   +---------------- re-plan if needed -----------+
```

In practice, this lets us:

- inspect only the most relevant temporal windows first,
- fall back to a broader search when evidence is missing,
- preserve intermediate evidence for debugging,
- and explain *why* a final answer was produced.

---

## Quick start

### 1) Environment setup

```bash
conda create -n avp python=3.10 -y
conda activate avp
conda install -c conda-forge ffmpeg
pip install -r requirements.txt
```

### 2) Configure API access

Create or update your config from `avp/config.example.json`.

If you keep secrets in a local shell file, you can source it before running AVP. For example:

```bash
source api_key_config.txt
```

This typically exports:

- `GEMINI_API_KEY`
- `GEMINI_BASE_URL`

### 3) Run a small evaluation

Single-sample debug run:

```bash
python -m avp.eval_dataset \
  --ann avp/eval_anno/eval_lvbench.json \
  --out avp/out/debug_single \
  --config avp/config.example.json \
  --limit 1 \
  --max-turns 1 \
  --timeout 600
```

Parallel evaluation:

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

Shell wrapper:

```bash
bash avp/parrelel_run.sh
```

---

## Useful repository entry points

- `avp/main.py` — core AVP controller and agent loop
- `avp/prompt.py` — prompt templates and JSON schemas
- `avp/video_utils.py` — video metadata, clipping, fallback helpers
- `avp/eval_dataset.py` — dataset-level evaluation runner
- `avp/eval_parallel.py` — subprocess-based parallel orchestration
- `gemini_q1_to_q5_report.md` — our full five-question demo report
- `avp/out/q1_to_q5_rerun_unified_20260321_180320/` — the main demo artifact directory

---

## Acknowledgement

This repository is our **demo-driven fork** of AVP, but the original research idea and initial codebase come from the AVP paper and project by Salesforce AI Research and collaborators.

Original project:

- GitHub: <https://github.com/SalesforceAIResearch/ActiveVideoPerception>
- Paper: <https://arxiv.org/abs/2512.05774>

If you use the original AVP research, please cite:

```bibtex
@misc{wang2025activevideoperceptioniterative,
  title={Active Video Perception: Iterative Evidence Seeking for Agentic Long Video Understanding},
  author={Ziyang Wang and Honglu Zhou and Shijie Wang and Junnan Li and Caiming Xiong and Silvio Savarese and Mohit Bansal and Michael S. Ryoo and Juan Carlos Niebles},
  year={2025},
  eprint={2512.05774},
  archivePrefix={arXiv},
  primaryClass={cs.CV},
  url={https://arxiv.org/abs/2512.05774}
}
```
