# ActiveVideoPerception 本地 Qwen 开放式 QA 方案与原始 AVP 实现差异分析报告

- 生成时间：2026-03-20
- 原始项目：`SalesforceAIResearch/ActiveVideoPerception`
- 本地项目路径：`/home/guoxiangyu/pytorch_project/ActiveVideoPerception/`
- 关键运行产物：
  - `avp/out/qwen_batch_eval/batch_20260319_083607/`
  - `avp/out/qwen_first_question_demo/Basketball_Full_001_1_5_20260319_101524/`
- 本报告关注点：
  1. Planner / Observer / Reflector 等角色的 prompt 设计差异
  2. 原始仓库的多项选择题（MCQ）回答框架，与本地开放式 QA 回答框架的差异
  3. 多角色交互后如何得出最终答案
  4. 搜索预算 / 停止条件 / 验证机制的差异
  5. 当前本地方案是否仍保持原始 AVP 的“机制级”结构

---

## 1. 结论先行（Executive Summary）

结论可以概括为一句话：**本地方案保留了原始 AVP 的 `plan -> observe -> reflect` 外层机制，但已经把“视频输入方式、最终答案契约、反思方式、正确性判定方式”四个核心实现层面显著改写为适配本地 `Qwen3.5-9B` 的开放式 QA 版本。**

换句话说：

- **在框架骨架上**，本地方案仍然是 AVP 风格；
- **在运行细节上**，它已经不是原始 Gemini + MCQ 的那条实现路径，而是一个 **Qwen 本地多轮开放式 QA 变体**。

更具体地说：

1. **Planner 设计基本保留原始仓库思路**：仍然是一轮只生成一个观察动作，仍然沿用 `PLAN_SCHEMA` 与原始 `get_planning_prompt()` / `get_replanning_prompt()` 体系。
2. **Observer 设计变化最大**：原始 AVP 是让 Gemini 直接看视频/clip；本地方案改成先采样帧，再切 chunk，再让 Qwen 逐 chunk 看高分辨率图像。
3. **Reflector 从启发式逻辑变成了显式 LLM 角色**：原始 `Reflector.reflect()` 主要依据证据数量和完整度给出启发式 `query_confidence`；本地方案则每轮都显式提示模型判断“证据是否足够回答问题”。
4. **最终回答从 MCQ 变成了真正的开放式 QA**：原始仓库即使在 open-ended 情况下也会强行走 `MCQ_SCHEMA`；本地方案改成了 `QA_SCHEMA`，直接产出自然语言答案。
5. **正确性判定机制新增了 Judger 角色**：原始仓库对 open-ended 只做非常弱的子串匹配；本地方案加入了“规则匹配 + 语义 Judger + 容错解析 + fail-closed”的新判定链路。
6. **预算与停止条件被显式扩展**：原始仓库默认 `max_turns=3`，并带自动 FPS 下调；本地方案把预算扩展到轮数、覆盖率、chunk、大图输入、答案置信度阈值等多个维度，并遵循“默认不自动降 FPS”的用户策略。

因此，从“是否沿用原论文/原仓库的多智能体机制”这个角度看，答案是：**是，机制被保留并且被强化了。**

但从“是否还是原始 AVP 那条代码路径”这个角度看，答案是：**不是。本地路径已经演化成一条新的、面向开放式 QA 与本地 Qwen 模型的实现。**

---

## 2. 对比范围与证据来源

本报告的对比基线来自两部分：

1. **本地仓库中仍保留的原始 AVP 主干实现**：
   - `avp/main.py`
   - `avp/prompt.py`
   - `avp/eval_dataset.py`
2. **本地新增/重构的 Qwen 开放式 QA 路径**：
   - `avp/qwen_first_question_demo.py`
   - `avp/qwen_batch_eval.py`

此外，还结合了运行产物进行验证：

- 五题 batch 产物：`avp/out/qwen_batch_eval/batch_20260319_083607/summary.json`
- 单题补跑产物：`avp/out/qwen_first_question_demo/Basketball_Full_001_1_5_20260319_101524/`

这意味着本报告并不是只从“设计意图”出发，而是同时参考了：

- 原始代码怎么写；
- 本地改造代码怎么写；
- 实际运行时生成了哪些 trace / summary / schema recovery 文件。

---

## 3. 原始 AVP 的机制骨架

原始 `ActiveVideoPerception` 的核心是一个单动作、可迭代的计划-观察-反思框架：

1. **Planner** 每轮只给出一个 observation action；
2. **Observer** 按该 action 去观察视频；
3. **Reflector** 判断当前证据是否足够；
4. 若不足，则重新规划下一轮；
5. 若足够或预算耗尽，则 **Synthesizer** 汇总最终答案。

对应代码位置：

- Controller 主循环：`avp/main.py:1787-1929`
- Reflector 逻辑：`avp/main.py:1422-1668`
- 最终综合回答：`avp/main.py:1182-1245`
- Planner schema：`avp/prompt.py:17-53`
- Observer schema：`avp/prompt.py:56-76`
- Reflector schema：`avp/prompt.py:79-97`
- Synthesis prompt / MCQ schema：`avp/prompt.py:607-664`, `avp/prompt.py:118-131`

值得注意的是，原始实现虽然在名称上有 Planner / Observer / Reflector / Synthesizer 这些角色，但其**真正强约束的角色契约主要集中在 Planner 与 Observer 上**；Reflector 和 open-ended answer 的部分并没有发展到本地方案这样严格。

---

## 4. 本地 Qwen 开放式 QA 路径的总体结构

本地方案的主执行文件是 `avp/qwen_first_question_demo.py`，它实现的是一条新的本地多轮 QA 路径。它保留了原始 AVP 的角色名和回路结构，但内部执行方式已经明显变化。

其一轮的典型链路为：

`Planner -> 采样视频帧 -> 按 chunk 调用 Observer -> 聚合证据 -> Synthesizer 生成暂定答案 -> Reflector 判断是否继续 -> Controller 决定 stop / replan -> 最后由 Judger 评估正确性`

关键代码位置：

- 开放式答案 prompt：`avp/qwen_first_question_demo.py:127-152`
- 正确性 Judger prompt：`avp/qwen_first_question_demo.py:155-193`
- 观察区域解析 / revisit 保留：`avp/qwen_first_question_demo.py:331-366`
- Observer chunk prompt：`avp/qwen_first_question_demo.py:448-489`
- 帧采样逻辑：`avp/qwen_first_question_demo.py:493-539`
- 角色重试 / schema recovery：`avp/qwen_first_question_demo.py:827-904`
- CLI 与预算参数：`avp/qwen_first_question_demo.py:1150-1183`
- 多轮 controller 主循环：`avp/qwen_first_question_demo.py:1197-1457`
- Judger 集成与 summary 输出：`avp/qwen_first_question_demo.py:1567-1674`

同时，批量执行器 `avp/qwen_batch_eval.py` 负责在一个 Python 进程中复用 Qwen 模型，避免每题重载权重：`avp/qwen_batch_eval.py:1-7`, `avp/qwen_batch_eval.py:154-205`。

---

## 5. 角色 prompt 设计差异

### 5.1 Planner：大体保留原始 AVP 设计

#### 原始实现

原始 Planner prompt 由 `PromptManager.get_planning_prompt()` 生成：`avp/prompt.py:142-329`。

它要求：

- 每轮只输出一个 observation action；
- 指定 `load_mode`（`uniform` / `region`）；
- 指定 `fps`；
- 指定 `spatial_token_rate`；
- 必要时指定 `regions`。

replan 时使用 `get_replanning_prompt()`（同文件后续段落），仍然沿用“一轮一动作”的思路。

#### 本地实现

本地 Qwen 路径并没有重新发明 Planner，而是直接在 controller 循环中继续调用原始 Planner prompt：

- 初轮：`PromptManager.get_planning_prompt(...)`，见 `avp/qwen_first_question_demo.py:1207-1213`
- 后续轮：`PromptManager.get_replanning_prompt(...)`，见 `avp/qwen_first_question_demo.py:1199-1206`

#### 结论

**Planner 是本地方案中保留原始 AVP 最完整的部分之一。**

变化主要不在 prompt 结构本身，而在输入任务类型：

- 原始框架经常面向 MCQ；
- 本地 SportsTime QA 路径传入 `options=None`，所以 Planner 不再围绕选项做定位，而是围绕开放式问题本身做观察计划。

因此可以说：**Planner 的角色定义基本保持原样，但服务对象从“多项选择题”切换到了“开放式问题”。**

---

### 5.2 Observer：从“直接看视频”变成“逐 chunk 看高分辨率帧”

#### 原始实现

原始 Observer prompt 由 `PromptManager.get_inference_prompt()` 构造：`avp/prompt.py:339-436`。

它的核心假设是：

- 模型可以直接接收视频或视频 clip；
- prompt 中会告诉模型当前分析的是哪一段视频；
- 输出符合 `EVIDENCE_SCHEMA`；
- 证据时间戳要统一映射回原始视频时间轴。

真正执行观察时，`GeminiClient` 会构造视频 part，并可能直接提交整段视频或若干 clip 给 Gemini：`avp/main.py:430-515` 及其后续观察逻辑。

#### 本地实现

本地方案彻底改变了 Observer 的输入方式：

1. 用 `sample_frames()` 按时间区间采样视频帧：`avp/qwen_first_question_demo.py:493-539`
2. 把帧切成多个 chunk：`avp/qwen_first_question_demo.py:1267-1269`
3. 每个 chunk 使用 `build_observer_chunk_prompt()` 单独发给 Qwen：`avp/qwen_first_question_demo.py:448-489`, `1277-1301`
4. 最后再调用 `aggregate_evidence_chunks()` 聚合为 round 级证据：`avp/qwen_first_question_demo.py:1332-1356`

新的 Observer prompt 有几个非常鲜明的特点：

- 明确告诉模型“**只分析当前提供的帧**”；
- 明确要求“如果目标事件不在当前 chunk 中，要明确说看不到”；
- 明确限制输出长度，避免长篇推理污染 JSON；
- 明确规定证据时间戳必须落在当前 chunk 范围内；
- 严格要求只输出 JSON。

#### 结论

**Observer 是改动最大、也是最本质的角色改造。**

原始 AVP 的 Observer 是“视频原生输入”的；本地 Qwen 版本的 Observer 是“图像 chunk 输入”的。两者仍然承担“观察并生成证据”的同一职责，但它们的 prompt 设计、输入媒介、失败模式和恢复策略都已经显著不同。

---

### 5.3 Reflector：从启发式 verifier 变成显式的 answerability 角色

#### 原始实现

原始仓库中虽然已经定义了 `REFLECTION_SCHEMA`：`avp/prompt.py:79-97`，也提供了 `get_answerability_reflection_prompt()`：`avp/prompt.py:545-604`，但在 `avp/main.py` 的主要运行路径里，`Reflector.reflect()` 并不是每轮都通过这个 prompt 去调用模型做判断。

相反，原始 `Reflector.reflect()` 的主体逻辑更接近一个启发式 verifier：

- 先从现有 evidence 中抽取 region：`avp/main.py:1563-1599`
- 如果没有 region，则直接认为证据不足：`avp/main.py:1609-1621`
- 如果有 evidence，则根据 evidence 条数和是否有详细描述估计 `query_confidence`：`avp/main.py:1623-1640`
- 再根据 `query_confidence > 0.5` 决定 `sufficient`：`avp/main.py:1642-1656`

这说明原始 Reflector 更像是一个**基于规则的 sufficiency estimator**，而不是一个 fully prompted LLM role。

#### 本地实现

本地 Qwen 路径则显式把 Reflector 当成一个独立的“回答性判断者”：

- 每轮都会构造 `get_answerability_reflection_prompt(...)`：`avp/qwen_first_question_demo.py:1388-1406`
- 再调用 `call_role_with_retries(..., schema=REFLECTION_SCHEMA)`：`avp/qwen_first_question_demo.py:1407-1415`

这个 prompt 明确要求模型回答：

- 当前证据是否足够；
- 若不够，下一轮搜索应选择 `broaden / shift / uniform / refine / stop` 中哪种策略；
- 不能把“有证据”错误等同于“能回答问题”；
- 反思必须保持机制级通用，而不是过拟合到某个数据集或篮球样例：`avp/prompt.py:586-599`

#### 结论

**Reflector 是本地方案中机制强化最明显的部分之一。**

原始 Reflector 偏启发式，本地 Reflector 偏显式 LLM 判断。这直接决定了本地方案为什么能够更自然地支持：

- 继续观看；
- 重新定位；
- 在证据不足时拒绝过早回答；
- 维持“机制通用性”而不写死在某个例子上。

---

## 6. 原始 MCQ 回答框架 vs 本地开放式 QA 回答框架

### 6.1 原始项目本质上是 MCQ-first

原始仓库的 synthesis prompt 是 `PromptManager.get_synthesis_prompt()`：`avp/prompt.py:607-664`。

关键点在于：**它无论有没有 options，最终都要求输出 `MCQ_SCHEMA`。**

当没有选项时，它仍然要求：

- 使用 `selected_option = "A"` 作为占位；
- 把真正答案放在 `selected_option_text` 和 `reasoning` 字段中：`avp/prompt.py:633-637`

相应地，`GeminiClient.synthesize_final_answer()` 也始终用 `MCQ_SCHEMA` 校验：`avp/main.py:1199-1225`。如果解析失败，fallback 仍然会构造一个 MCQ 形态的对象：`avp/main.py:1233-1245`。

这说明：**原始 AVP 的最终回答接口并没有真正分化出 open-ended QA 的独立 schema。**

### 6.2 原始 open-ended 评测很弱

在 `avp/eval_dataset.py` 中：

- MCQ 首先抽取 `selected_option`：`avp/eval_dataset.py:63-74`
- open-ended 情况下，只做简单字符串包含判断：`avp/eval_dataset.py:271-274`

也就是说，只要 `answer.lower()` 出现在 `final_answer_data` 的字符串展开里，就会被判定为正确。这对于开放式 QA 来说显然非常弱。

### 6.3 本地方案改成真正的开放式 QA 合同

本地 `qwen_first_question_demo.py` 直接引入开放式 QA synthesis prompt：

- `build_open_qa_synthesis_prompt()`：`avp/qwen_first_question_demo.py:127-152`
- 返回 schema 为 `QA_SCHEMA`（字段为 `answer`, `confidence`, `reasoning`）

在 controller 中，Synthesizer 每一轮都输出一个暂定开放式答案：

- 调用位置：`avp/qwen_first_question_demo.py:1372-1386`
- 最终使用 `predicted_answer = synthesis_data["answer"]`：`avp/qwen_first_question_demo.py:1567-1572`

#### 结论

**本地方案已经把“最终回答的基本契约”从 MCQ 彻底改成了开放式 QA。**

这是与原始仓库最关键的差异之一。

---

## 7. 最终答案是如何由多角色交互得出的

### 7.1 原始仓库：最终回答更接近“末轮综合”

在原始 AVP 中，最终答案主要来自两种路径：

1. 在最后一轮由 Reflector 直接触发 final answer 生成：`avp/main.py:1474-1551`
2. 或在 controller 结束后由 `synthesize_final_answer()` 汇总生成：`avp/main.py:1182-1245`, `avp/main.py:1900-1929`

也就是说，原始实现的回答更偏向：

- 前几轮主要找证据；
- 最后一轮或循环结束时统一回答。

### 7.2 本地方案：每轮先给 provisional answer，再由 Reflector 判断是否停

本地 controller 的顺序更明确：

1. Planner 生成一个观察动作：`avp/qwen_first_question_demo.py:1197-1224`
2. 解析观察时间段，并保留显式 region revisit：`avp/qwen_first_question_demo.py:1225-1234`, `331-366`
3. 采样帧、切 chunk、调用 Observer：`1236-1356`
4. 聚合该轮 evidence：`1332-1356`
5. Synthesizer 基于当前所有 evidence 生成暂定答案：`1371-1386`
6. Reflector 判断该答案是否已经“可回答”：`1388-1415`
7. Controller 根据回答置信度、视频覆盖率、最大轮数来决定是否停止：`1445-1457`

停止条件清晰如下：

- 若 `reflection_data.sufficient == True` 且 `provisional_confidence >= answer_confidence_threshold`，停止：`1445-1449`
- 若覆盖率 `>= 0.995`，停止：`1450-1453`
- 若轮数达到 `max_rounds`，停止：`1454-1457`

#### 结论

**本地方案的最终答案不是简单的“最后才答一次”，而是“每轮都生成可审视的暂定答案，再由 Reflector 决定是否允许它成为最终答案”。**

这使得角色间交互更丰富，也更符合你要求的“证据不足就继续看、继续重定位、继续重解释”的机制。

---

## 8. 搜索预算、停止条件与视频观察预算的差异

### 8.1 原始仓库预算更简单：`max_turns=3` + 自动 FPS 下调

原始 dataset evaluator 的默认预算是：

- `--max-turns=3`：`avp/eval_dataset.py:381-388`
- controller 主循环默认 `max_rounds=3`：`avp/main.py:1787-1805`

此外，原始 Gemini 路径还内置了 frame 上限：

- `max_frame_low=512`
- `max_frame_medium=128`
- `max_frame_high=128`

定义位置：`avp/main.py:369-371`

更重要的是，当预计帧数超过上限时，原始实现会自动调低 FPS：

- 计算 `expected_frames = fps * duration`：`avp/main.py:492-495`
- 若超限则 `fps = adjusted_fps`：`avp/main.py:495-499`

因此，原始 AVP 的预算逻辑更像是：

- 轮数少；
- 每轮用 Gemini 原生视频能力观察；
- 通过自动下调 FPS 控制成本。

### 8.2 本地方案预算是多维的，而且默认不自动降 FPS

本地 `qwen_first_question_demo.py` 的关键默认预算是：

- `--max-frames=0`：不限制 sampled frames 上限，保持请求 FPS；`avp/qwen_first_question_demo.py:1150-1153`
- `--image-max-side=0`：默认保持原图尺寸；`1154-1159`
- `--observer-chunk-size=2`：每次只送少量高分辨率帧给 Observer；`1160-1165`
- `--max-rounds=12`：默认允许更多 plan-observe-reflect 轮次；`1166-1171`
- `--uniform-window-sec=180.0`：通用定位扫描窗口；`1172-1177`
- `--answer-confidence-threshold=0.75`：早停阈值；`1178-1183`

批量执行器 `qwen_batch_eval.py` 也把这些预算显式暴露出来：`avp/qwen_batch_eval.py:189-203`

这体现了本地策略与原始策略的一个核心分歧：

- 原始仓库更倾向于**自动压缩输入预算**；
- 本地方案在你的要求下，改成**尽量保留清晰度，用 chunking、更多时间和更多轮数来换内存安全**。

### 8.3 实际五题 batch 的预算快照

`avp/out/qwen_batch_eval/batch_20260319_083607/summary.json` 记录的 batch 摘要为：

- `requested = 5`
- `processed = 5`
- `successful = 4`
- `errors = 1`
- `correct = 0`
- `accuracy_on_successful = 0.0`

这个结果说明：

- **稳定性** 已经比早期版本显著提升，但仍未完全消灭异常；
- **准确率** 仍然是当前主要瓶颈；
- 机制上的“多轮继续看”已经存在，但“找到正确答案”的能力还需要进一步校准。

---

## 9. 新增的 Judger 角色与验证机制

### 9.1 原始项目的 open-ended correctness 很弱

如前所述，原始 `avp/eval_dataset.py` 对 open-ended 的正确性几乎只做字符串包含匹配：`avp/eval_dataset.py:271-274`。

这对于 SportsTime 这种自由问答任务远远不够，因为：

- 同义改写会被误判；
- 答案可能部分正确但表达不同；
- 有些错误答案也可能碰巧包含 reference 关键词。

### 9.2 本地方案引入了单独的 Judger 角色

本地 Qwen 路径新增了：

- `build_correctness_judge_prompt()`：`avp/qwen_first_question_demo.py:155-193`
- `parse_judger_output()`：`1006-1065`
- `call_judger_with_tolerant_parsing()`：`1068-1110`
- 在 controller 收尾阶段调用 Judger：`1571-1591`

Judger 的判定链路是分层的：

1. 先用规则匹配：`exact_or_rule_match()`，如 exact / substring：`926-941`
2. 若规则无法决定，再调用 LLM Judger：`1580-1590`
3. 解析时允许：
   - JSON verdict
   - `Verdict: Correct/Incorrect`
   - 中文“正确/错误”
   - tail-window 关键字推断：`968-1065`
4. 若仍无法解析，则走 fail-closed：
   - `semantic_judger_no_verdict`
   - 直接记为 incorrect：`1095-1110`

Judger prompt 还明确说明：

- 判断的是**语义正确性**，而不是字面一致性；
- `CoT` 仅用于 judging context，不是生成目标：`avp/qwen_first_question_demo.py:172-183`

#### 结论

**Judger 是本地方案相对原始 AVP 新增的核心角色。**

它并不直接参与视频搜索，但直接决定了开放式 QA 的评测可靠性。这是本地方案必须新增的部分，因为原始 AVP 的 MCQ-first 评测逻辑不足以覆盖你的任务目标。

---

## 10. 角色协议（interaction protocol）层面的变化

### 10.1 原始协议

原始协议比较简洁：

- Planner 给一个动作；
- Observer 执行动作；
- Reflector 决定是否足够；
- 若足够则 Synthesizer 生成最终答案。

它有 history 与 evidence 持久化，但没有把“每个角色必须通过严格合同校验、重试、回退恢复”做成运行时的强流程。

### 10.2 本地协议：加入了重试、恢复与可审计 trace

本地 `call_role_with_retries()` 为 Planner / Observer / Reflector / Synthesizer 都加入了统一协议：

- 先附加 strict JSON guard：`avp/qwen_first_question_demo.py:837-845`
- 若解析或 schema 校验失败，则追加 repair prompt 重试：`856-864`
- 若仍失败：
  - Observer 走 `fallback_evidence_from_unstructured_raw()`：`865-877`
  - Reflector 走 `fallback_reflection_from_unstructured_raw()`：`878-890`
  - Synthesizer 走 `fallback_qa_from_unstructured_raw()`：`891-903`

同时，本地方案会把每个角色的 trace 写盘：

- `*.prompt.txt`
- `*.raw_response.attemptN.txt`
- `*.parsed.json`
- `*.schema_recovery.json`（若触发）

最终还会在 `summary.json` 中写出：

- `role_contract_status`
- `schema_recoveries`
- `trace_available`

对应位置：`avp/qwen_first_question_demo.py:1650-1671`

#### 结论

**本地方案的角色交互协议，已经从“调用一个角色函数”升级成“角色合同校验 + 重试 + 可恢复 + 可追踪”的运行机制。**

这一点非常重要，因为本地 Qwen 的格式漂移问题比原始 Gemini 路径明显得多，必须通过协议强化来提高系统稳定性。

---

## 11. Observer 的重新观看 / 重新定位 / 重新解释机制

你特别强调过：系统不能只做一次局部观察，而要在证据不足时继续看、重定位、重解释。

这部分本地实现已经显式加入了机制级支持。

### 11.1 保留显式 region revisit

`resolve_observation_span()` 中有一个关键修正：

- 若 Planner 明确给出了 `region` 且提供了具体 `regions`，则直接保留该区域，不再被 overlap-guard 强行重写：`avp/qwen_first_question_demo.py:348-352`

这意味着：

- Planner 可以有意识地“回看某段区域”；
- 系统不会因为“这段看过了”就机械地跳走；
- 从机制上允许 reinterpretation，而不是只允许扫没看过的区间。

### 11.2 通用的停止条件而非样例特判

本地 controller 停止依赖的是：

- 反思认为证据足够；
- 暂定答案置信度达到阈值；
- 全视频覆盖率接近耗尽；
- 或轮数预算耗尽。

对应代码：`avp/qwen_first_question_demo.py:1445-1457`

这比“看见一点证据就回答”更通用，也更符合你要求的“不要针对某个篮球问题硬编码”的目标。

---

## 12. 批量框架设计差异：原始 dataset eval vs 本地 in-process Qwen batch

### 原始框架

原始 `avp/eval_dataset.py` 的思路是：

- 逐样本调用 controller；
- 默认 `--max-turns=3`；
- 输出 correctness 汇总。

它更像一个“原型性质的 dataset evaluator”。

### 本地框架

本地 `avp/qwen_batch_eval.py` 则专门针对本地大模型做了工程化处理：

- 一个 Python 进程内缓存 Qwen runner：`avp/qwen_batch_eval.py:3-7`, `21`
- 每题独立日志：`80-151`
- batch 级 `results.jsonl` 与 `summary.json`：`224-281`
- 把高分辨率 chunk、轮数、置信阈值等参数统一向下透传：`53-77`

这说明本地方案不仅改变了 agent 逻辑，也改变了**大规模评测时的工程运行方式**。

---

## 13. 本地方案相对原始 AVP 的“保留”与“偏离”

### 13.1 保留的部分

以下内容仍明显继承自原始 AVP：

1. **单步 Planner**：每轮只生成一个动作；
2. **Plan-Observe-Reflect 外层循环**；
3. **基于时间区间的观察与重规划**；
4. **证据驱动的最终回答，而不是无条件一-shot 回答**；
5. **round 级持久化和可审计运行目录**。

### 13.2 偏离的部分

以下内容已经明显偏离原始实现：

1. **从 Gemini 原生视频输入改成 Qwen 图像 chunk 输入**；
2. **从 MCQ 最终答案契约改成开放式 QA 契约**；
3. **从启发式 Reflector 改成显式 answerability Reflector**；
4. **新增 Judger 作为语义正确性判定角色**；
5. **新增严格 schema 重试 / fallback / fail-closed 机制**；
6. **默认不自动降 FPS，而是优先保清晰度并用 chunk 降显存风险**。

因此，本地项目应被更准确地描述为：

> **A Qwen-based open-ended AVP variant**
>
> 即：保留 AVP 机制骨架、但在回答和验证层做了重构的本地开放式 QA 变体。

---

## 14. 运行结果对这一比较的支持

五题 batch 产物 `avp/out/qwen_batch_eval/batch_20260319_083607/summary.json` 显示：

- `successful = 4`
- `errors = 1`
- `correct = 0`
- `accuracy_on_successful = 0.0`

这说明本地方案当前处于这样一个状态：

1. **机制层面已显著丰富**：
   - 多轮控制、重定位、重解释、Judger、schema recovery 都已经存在；
2. **稳定性层面明显优于早期版本**：
   - observer / reflector / synthesizer 都已有 recovery path；
3. **准确率层面仍然薄弱**：
   - 说明从“能跑通多智能体结构”到“真正答对问题”之间仍有明显距离。

也就是说，这份对比报告的核心结论并不是“本地方案已经比原始方案更强”，而是：

- 它已经在机制上更适合你的 SportsTime 开放式 QA 任务；
- 但在结果质量上，仍需继续校准与迭代。

---

## 15. 最终判断：这套本地方案是否符合你的目标？

如果你的目标是：

- 保留原始 AVP 的多智能体机制；
- 让系统在证据不足时继续看视频、重新定位、重新解释；
- 用本地 `Qwen3.5-9B` 跑开放式视频 QA；
- 对答案正确性做比 substring 更可靠的语义判断；

那么当前本地方案的回答是：**方向上是对的，且关键机制已经落地。**

但如果你的目标进一步提高为：

- 在 200 题 SportsTime 上取得“相对理想”的准确率；

那么当前系统还没有达到该目标。现阶段它更像是：

- **机制框架已基本成形**；
- **稳定性明显改善**；
- **正确率仍需集中攻坚**。

---

## 16. 建议的后续工作（按优先级）

1. **先继续提升 answer quality，而不是再继续增加角色数量。**
   - 当前最大的短板不在“缺少角色”，而在“已有角色如何更可靠地定位、读图、整合证据”。

2. **重点优化 Planner 与 Reflector 的协同质量。**
   - 现在多轮机制已经有了，但“下一轮去哪看、为什么去、是否值得回看”仍有提升空间。

3. **针对 SportsTime QA 做系统性 error taxonomy。**
   - 例如区分：定位失败、观察失败、综合失败、Judger 误判、coverage 用尽但未定位等。

4. **在保持“默认不降 FPS”的前提下，继续优化 chunk 策略。**
   - 例如基于运动密度或镜头变化自适应地设定 chunk 边界，而不是只按固定帧数切分。

5. **在全 200 题跑之前，先拿 20-30 题做受控校准。**
   - 当前五题结果说明直接上 200 题还为时过早，先做中等规模校准更合适。

---

## 17. 附录：本次报告重点引用的文件位置

### 原始 AVP 路径（基线）

- `avp/prompt.py:17-53` — `PLAN_SCHEMA`
- `avp/prompt.py:56-76` — `EVIDENCE_SCHEMA`
- `avp/prompt.py:79-97` — `REFLECTION_SCHEMA`
- `avp/prompt.py:142-329` — `get_planning_prompt()`
- `avp/prompt.py:339-436` — `get_inference_prompt()`
- `avp/prompt.py:545-604` — `get_answerability_reflection_prompt()`
- `avp/prompt.py:607-664` — `get_synthesis_prompt()`（仍为 MCQ 形态）
- `avp/main.py:430-515` — Gemini 视频输入与自动 FPS 调整
- `avp/main.py:1182-1245` — `synthesize_final_answer()`
- `avp/main.py:1422-1668` — `Reflector.reflect()`
- `avp/main.py:1787-1929` — `Controller.run()`
- `avp/eval_dataset.py:252-274` — correctness 逻辑，open-ended 仅做 substring
- `avp/eval_dataset.py:381-388` — `--max-turns=3`

### 本地 Qwen 开放式 QA 路径

- `avp/qwen_first_question_demo.py:127-152` — `build_open_qa_synthesis_prompt()`
- `avp/qwen_first_question_demo.py:155-193` — `build_correctness_judge_prompt()`
- `avp/qwen_first_question_demo.py:331-366` — `resolve_observation_span()`，保留 region revisit
- `avp/qwen_first_question_demo.py:448-489` — `build_observer_chunk_prompt()`
- `avp/qwen_first_question_demo.py:493-539` — `sample_frames()`
- `avp/qwen_first_question_demo.py:827-904` — `call_role_with_retries()` 与 schema fallback
- `avp/qwen_first_question_demo.py:1006-1110` — Judger tolerant parsing / fail-closed
- `avp/qwen_first_question_demo.py:1150-1183` — 预算参数（`max_frames`, `image_max_side`, `observer_chunk_size`, `max_rounds`, `uniform_window_sec`, `answer_confidence_threshold`）
- `avp/qwen_first_question_demo.py:1197-1457` — 主 controller 循环
- `avp/qwen_first_question_demo.py:1567-1674` — Judger 集成与最终 summary 输出
- `avp/qwen_batch_eval.py:1-7` — in-process runner cache 设计
- `avp/qwen_batch_eval.py:53-77` — runner 参数透传
- `avp/qwen_batch_eval.py:154-205` — batch CLI 参数
- `avp/qwen_batch_eval.py:224-281` — batch results / summary 聚合

---

## 18. 一句话总结

**本地方案不是简单地把原始 AVP“换个模型”而已；它是在保留 AVP 外层多智能体控制机制的前提下，把内部实现重构成了一个更适合本地 `Qwen3.5-9B` 与开放式视频 QA 的新版本。**

目前它已经更接近你要的“可持续观看、可重新定位、可重新解释、可语义判分”的 agent framework，但距离“200 题上取得理想结果”还有明显优化空间。
