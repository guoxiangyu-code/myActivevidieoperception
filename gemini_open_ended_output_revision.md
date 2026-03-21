# Gemini Open-Ended Output Revision Report

This document records the code change that converts the original Gemini path from pseudo-MCQ open-QA output into a true open-ended answer format.

## Why the change was needed

Before this revision, the original Gemini synthesis path treated *all* QA as MCQ during the final answer step.

That behavior came from these locations:

- `avp/prompt.py` — `PromptManager.get_synthesis_prompt(...)`
- `avp/main.py` — `GeminiClient.synthesize_final_answer(...)`
- `avp/main.py` — `Reflector.reflect(..., is_last_round=True, ...)`
- `avp/main.py` — `Controller.run(...)` final-answer text extraction
- `avp/eval_dataset.py` — `extract_answer(...)`

In the pre-change path, an open-ended answer like `2次` was returned as:

```json
{
  "selected_option": "A",
  "confidence": 0.95,
  "reasoning": "比赛开始后，俄克拉荷马城雷霆队通过三次有效的进攻得到了前8分。首先是拉塞尔·威斯布鲁克在128.0s-132.0s通过上篮得到2分（2-0）；接着是凯文·杜兰特在155.0s-159.0s命中一记三分球，得到3分（5-0），这是第一次三分出手；随后安德烈·罗伯森在184.0s-187.0s再次命中一记三分球，得到3分，使总得分达到8分（8-0），这是第二次三分出手。根据证据显示，在此过程中雷霆队总共完成了2次三分球出手，且两次均命中。",
  "selected_option_text": "2次",
  "query_confidence": 0.8,
  "query": "比赛开始后，俄克拉荷马城雷霆队在得到前8分的过程中，总共完成了多少次三分出手？"
}
```

That format is semantically usable, but it is not a real open-ended contract.

## What changed

### 1. Prompt layer

File: `avp/prompt.py`

- Open-ended synthesis no longer instructs the model to use placeholder option `A`.
- When `options=[]`, the prompt now requests `FINAL_ANSWER_SCHEMA` instead of `MCQ_SCHEMA`.
- The open-ended example now uses:
  - `answer`
  - `key_timestamps`
  - `confidence`
  - `evidence_summary`

### 2. Runtime normalization layer

File: `avp/main.py`

Added helpers:

- `is_mcq_options(...)`
- `normalize_final_answer_output(...)`

Behavior after the change:

- If options exist, the runtime still uses MCQ output.
- If options do not exist, the runtime now validates against `FINAL_ANSWER_SCHEMA`.
- If the model responds unstructured for open QA, the fallback now produces an open-ended object rather than fabricating `selected_option="A"`.

### 3. Final answer persistence

File: `avp/main.py`

`Controller.run(...)` now derives `plan.final_answer` in this order:

1. `answer`
2. `selected_option_text`
3. `reasoning`
4. `evidence_summary`

This keeps both MCQ and open-ended tasks compatible.

### 4. Evaluation extraction

File: `avp/eval_dataset.py`

`extract_answer(...)` now prefers `answer` for open-ended results, so dataset evaluation will record the natural-language answer itself instead of placeholder option letters.

## Before vs after

| Aspect | Before | After |
| --- | --- | --- |
| Open-ended synthesis schema | `MCQ_SCHEMA` | `FINAL_ANSWER_SCHEMA` |
| Required answer field | `selected_option_text` | `answer` |
| Placeholder option letter | Required (`A`) | Not used |
| Controller final answer extraction | preferred `selected_option_text` | preferred `answer` |
| Open-QA result readability | awkward | natural |

## Local validation performed

Because this shell environment cannot make outbound Gemini API calls, I validated the revised path locally in a code-path-safe way.

### Validation command

```bash
cd /home/guoxiangyu/pytorch_project/ActiveVideoPerception
conda run -n ActiveVideoPerception python -m py_compile avp/prompt.py avp/main.py avp/eval_dataset.py
```

### Schema-path regression check

I also ran a local Python assertion script that verified:

1. the open-ended synthesis prompt no longer contains `selected_option` as the required answer field,
2. the prompt now includes `"answer"`,
3. `normalize_final_answer_output(...)` preserves an open-ended answer object,
4. `extract_answer(...)` returns the direct answer string (`2次`) for open-ended output.

### Synthetic revised result (local code-path test)

Using the revised normalization path with the real answer content from this Q2 case yields:

```json
{
  "answer": "2次",
  "key_timestamps": [
    155,
    184
  ],
  "confidence": 0.95,
  "evidence_summary": "两次三分命中。",
  "query_confidence": 0.8
}
```

This is the target shape the live Gemini run should now produce for this question.

## Live re-test status

A true live Gemini re-test was **not executable from this shell** because outbound network calls are blocked in this environment.

So the current status is:

- code revision: **completed**
- local compilation: **passed**
- local schema-path validation: **passed**
- live Gemini API re-test from this environment: **blocked by network restriction**

## Recommended local re-test command

To verify the new format on your machine, rerun the second-question sample with the same environment variables you already used:

```bash
cd /home/guoxiangyu/pytorch_project/ActiveVideoPerception

conda run -n ActiveVideoPerception python -m avp.eval_dataset   --config avp/config.gemini_yunwu_first5.example.json   --ann /tmp/basketball_second_question_enriched.json   --out avp/out/gemini_yunwu_q2_openended   --limit 1   --max-turns 3
```

After that, check:

- `avp/out/gemini_yunwu_q2_openended/all_sample/sample_0/final_answer.json`
- `avp/out/gemini_yunwu_q2_openended/results.jsonl`

The revised `final_answer.json` should now be shaped like:

```json
{
  "answer": "2次",
  "key_timestamps": [...],
  "confidence": ...,
  "evidence_summary": "...",
  "query_confidence": ...
}
```
