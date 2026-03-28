# AthenaQA（from ActiveVideoPerception）
## 基于 Gemini-2.5-Pro 的长视频问答框架（多个agent代理）

<div align="center">

[![Demo Report](https://img.shields.io/badge/Report-Gemini%205Q%20Demo-4F46E5)](gemini_q1_to_q5_report.md)
[![Manual Review](https://img.shields.io/badge/Manual%20Review-4%2F5%20Correct-16A34A)](#demo-overview)
[![Only Unresolved](https://img.shields.io/badge/Open%20Issue-Q4-F97316)](#q4-demo)
[![Model](https://img.shields.io/badge/Model-gemini--2.5--pro-0EA5E9)](gemini_q1_to_q5_report.md)
[![Framework](https://img.shields.io/badge/Framework-AVP-DC2626)](https://github.com/SalesforceAIResearch/ActiveVideoPerception)

</div>

<div align="center">
  <table>
    <tr>
      <td align="center"><img src="assets/teaser.png" width="420" alt="AVP teaser"></td>
      <td align="center"><img src="assets/vis.png" width="420" alt="AVP workflow"></td>
    </tr>
  </table>
</div>

<div align="center">

**本仓库 Demo 整理与 README 作者：Guoxiangyu × GitHub Copilot**  
**基于原始项目：** [SalesforceAIResearch / ActiveVideoPerception](https://github.com/SalesforceAIResearch/ActiveVideoPerception)

</div>

---

## ✨ 一页看懂这个仓库

这个仓库不是单纯保留论文代码，而是把 **AVP（Active Video Perception）** 做成了一个可复现、可检查、可展示的 **长视频问答 Demo**。

我们当前重点展示的是一组 **SportsTime 篮球长视频问答样例**，并且把每一题对应的：

- Planner 规划结果
- Observer 证据提取
- Reflector / Verifier 判定
- Synthesizer 最终答案
- 运行日志与中间产物

都完整保存在仓库里，方便直接打开查看。
---

## 📌 Demo 关键信息面板

<table>
  <tr>
    <td align="center"><strong>Demo 模型</strong><br><code>gemini-2.5-pro</code></td>
    <td align="center"><strong>数据</strong><br>SportsTime Basketball</td>
    <td align="center"><strong>视频</strong><br><code>Basketball_Full_001_1.mp4</code></td>
  </tr>
  <tr>
    <td align="center"><strong>运行目录</strong><br><code>avp/out/q1_to_q5_rerun_unified_20260321_180320</code></td>
    <td align="center"><strong>自动 exact-match</strong><br><code>1 / 5</code></td>
    <td align="center"><strong>人工语义复核</strong><br><code>4 / 5</code></td>
  </tr>
  <tr>
    <td align="center"><strong>当前唯一未通过</strong><br><code>Q4</code></td>
    <td align="center"><strong>完整报告</strong><br><a href="gemini_q1_to_q5_report.md">gemini_q1_to_q5_report.md</a></td>
    <td align="center"><strong>结果文件</strong><br><a href="avp/out/q1_to_q5_rerun_unified_20260321_180320/results.jsonl">results.jsonl</a></td>
  </tr>
</table>

### 为什么自动评测只有 1/5，但我们人工判断是 4/5？

因为当前 `summary.json` / `results.jsonl` 使用的是 **严格字符串 exact-match**。

这会把下面这些“语义正确但表述更完整”的答案也判错：

1. **Q1**：模型不仅说到了“被掩护挡住”，还进一步解释了后续拉人犯规动作；
2. **Q3**：模型回答里包含“个人运球创造投篮空间”这一核心机制，但额外提到了换防错位背景；
3. **Q5**：模型给出的是 `83 秒`，而参考答案写法是 `约75秒到90秒之间`，两者在语义上是一致的。

因此，我们把这次 Demo 的展示重点放在：

- **证据链是否闭合**
- **最终回答是否抓住核心语义**
- **系统是否真正找到了正确回合**

而不仅仅是最终字符串是否逐字一致。

---

<a id="demo-overview"></a>

## 🏀 五题 Demo 总览

| Q | 类型 | Planner 最终策略 | 关键证据时间 | 模型结论摘要 | 自动评测 | 人工复核 | Demo |
|---|------|------------------|--------------|--------------|----------|----------|------|
| **Q1** | 因果推理 | `region [[93, 128]]` | `120-123s`, `127-130s` | 已抓住“库里在防守掩护时被挡住”的核心因果链  | ✅ | [`sample_0`](avp/out/q1_to_q5_rerun_unified_20260321_180320/all_sample/sample_0/) |
| **Q2** | 感知计数 | `uniform full-video` | `155-158s`, `184-187s` | 正确数出雷霆前 8 分过程中的两次三分出手 | ✅ | [`sample_1`](avp/out/q1_to_q5_rerun_unified_20260321_180320/all_sample/sample_1/) |
| **Q3** | 战术推理 | `region [[245, 275]]` | `270-274s` | 已明确回答“个人运球创造投篮空间”这一主因 | ✅ | [`sample_2`](avp/out/q1_to_q5_rerun_unified_20260321_180320/all_sample/sample_2/) |
| **Q4** | 反事实推理 | `uniform full-video` | `1810-1813s`, `1824-1829s` | 系统搜到最接近真实回合，但题干与原视频不完全匹配  | ❌ | [`sample_3`](avp/out/q1_to_q5_rerun_unified_20260321_180320/all_sample/sample_3/) |
| **Q5** | 时序推理 | `region [[0, 240]]` | `103-187s` | 给出 `83 秒`，与证据链和参考区间一致 | ✅ | [`sample_4`](avp/out/q1_to_q5_rerun_unified_20260321_180320/all_sample/sample_4/) |

---

## 🔍 五题逐项展示

### Q1 — 因果推理（我们判定：✅ 正确）

<details>
<summary><strong>展开查看 Q1 细节</strong></summary>

**问题**  
比赛刚开始，金州勇士队30号球员（斯蒂芬·库里）对俄克拉荷马城雷霆队35号球员（凯文·杜兰特）犯规，最直接的原因是什么？

**参考答案**  
`金州勇士队30号球员在防守掩护时被挡住`

**模型答案（原文）**  
> 最直接的原因是，在防守凯文·杜兰特（35号）利用无球掩护跑位时，斯蒂芬·库里（30号）被掩护队员阻挡，为了阻止杜兰特摆脱自己，库里伸手拉拽了杜兰特的身体，构成了一次拉人犯规（Holding Foul）。

**Planner**  
- 观察窗口：`[93s, 128s]`
- 轮数：`1`

**关键证据**  
- `[120, 122]s`：杜兰特利用无球掩护跑动；
- `[121, 123]s`：库里追防时被挡住并发生犯规；
- `[127, 130]s`：慢动作回放确认库里绕掩护时的拉人动作。

**为什么我们判定它正确**  
这道题的关键不在于“犯规动作叫什么”，而在于系统有没有抓住 **更上游的真实原因：库里在防守掩护时被挡住**。从答案原文看，这一因果链已经被清楚表达出来，因此我们把它算作语义正确。

**可直接打开的产物**

- [`plan.initial.json`](avp/out/q1_to_q5_rerun_unified_20260321_180320/all_sample/sample_0/plan.initial.json)
- [`evidence.json`](avp/out/q1_to_q5_rerun_unified_20260321_180320/all_sample/sample_0/evidence/round_1/evidence.json)
- [`final_answer.json`](avp/out/q1_to_q5_rerun_unified_20260321_180320/all_sample/sample_0/final_answer.json)
- [`conversation_history.json`](avp/out/q1_to_q5_rerun_unified_20260321_180320/all_sample/sample_0/conversation_history.json)
- [`role_traces.jsonl`](avp/out/q1_to_q5_rerun_unified_20260321_180320/all_sample/sample_0/role_traces.jsonl)

</details>

### Q2 — 感知计数（我们判定：✅ 正确）

<details>
<summary><strong>展开查看 Q2 细节</strong></summary>

**问题**  
比赛开始后，俄克拉荷马城雷霆队在得到前8分的过程中，总共完成了多少次三分出手？

**参考答案**  
`2次`

**模型答案（原文）**  
> 比赛开始后，俄克拉荷马城雷霆队在得到前8分的过程中，总共完成了2次三分出手。

**Planner**  
- 策略：`uniform full-video`
- 轮数：`1`

**关键证据**  
- `[155, 158]s`：杜兰特命中第一记三分，比分到 `5-0`；
- `[184, 187]s`：罗伯森命中第二记三分，比分到 `8-0`。

**为什么我们判定它正确**  
答案、证据和计数逻辑完全一致，是这组 Demo 里最稳定的一题。

**可直接打开的产物**

- [`plan.initial.json`](avp/out/q1_to_q5_rerun_unified_20260321_180320/all_sample/sample_1/plan.initial.json)
- [`evidence.json`](avp/out/q1_to_q5_rerun_unified_20260321_180320/all_sample/sample_1/evidence/round_1/evidence.json)
- [`final_answer.json`](avp/out/q1_to_q5_rerun_unified_20260321_180320/all_sample/sample_1/final_answer.json)
- [`conversation_history.json`](avp/out/q1_to_q5_rerun_unified_20260321_180320/all_sample/sample_1/conversation_history.json)
- [`role_traces.jsonl`](avp/out/q1_to_q5_rerun_unified_20260321_180320/all_sample/sample_1/role_traces.jsonl)

</details>

### Q3 — 战术推理（我们判定：✅ 正确）

<details>
<summary><strong>展开查看 Q3 细节</strong></summary>

**问题**  
金州勇士队30号球员在比赛开局阶段命中三分球，这个得分机会主要是通过哪种方式创造出来的？

**参考答案**  
`利用个人运球技巧创造出投篮空间`

**模型答案（原文）**  
> 这个得分机会主要是通过利用防守换防造成的错位，进行个人运球单打创造出来的。金州勇士队30号球员斯蒂芬·库里在一次一对一进攻中，面对对方身材高大的前锋凯文·杜兰特，通过背后运球和交叉步运球等个人技巧，成功晃开防守，为自己创造了充足的投篮空间并命中三分球。

**Planner**  
- 观察窗口：`[245s, 275s]`
- 轮数：`1`

**关键证据**  
- `[270, 272]s`：形成错位单打；
- `[272, 273]s`：库里通过背后运球、交叉步等动作创造空间；
- `[273, 274]s`：库里完成三分出手并命中。

**为什么我们判定它正确**  
这道题的核心是“主要通过什么方式创造得分机会”。模型明确写出了 **个人运球技巧创造投篮空间**，只是额外补充了“换防错位”这一背景信息。我们认为这不改变答案主旨。

**可直接打开的产物**

- [`plan.initial.json`](avp/out/q1_to_q5_rerun_unified_20260321_180320/all_sample/sample_2/plan.initial.json)
- [`evidence.json`](avp/out/q1_to_q5_rerun_unified_20260321_180320/all_sample/sample_2/evidence/round_1/evidence.json)
- [`final_answer.json`](avp/out/q1_to_q5_rerun_unified_20260321_180320/all_sample/sample_2/final_answer.json)
- [`conversation_history.json`](avp/out/q1_to_q5_rerun_unified_20260321_180320/all_sample/sample_2/conversation_history.json)
- [`role_traces.jsonl`](avp/out/q1_to_q5_rerun_unified_20260321_180320/all_sample/sample_2/role_traces.jsonl)

</details>

<a id="q4-demo"></a>

### Q4 — 反事实推理（唯一未通过样例）

<details>
<summary><strong>展开查看 Q4 细节</strong></summary>

**问题**  
在第二节初期，俄克拉荷马城雷霆队0号球员（拉塞尔·威斯布鲁克）成功突破金州勇士队5号球员（马里斯·斯贝茨）的防守并上篮得分。如果当时勇士队5号球员在防守时没有试图去“抢断”，而是选择“保持防守位置”，那么最可能发生什么？

**参考答案**  
`雷霆队0号球员的进攻会受到更大的干扰，可能无法轻松得分`

**模型答案（原文摘要）**  
> 模型先指出题干场景与原视频存在出入，然后在全片 fallback 后找到一个最接近的真实回合，并据此给出“威少仍大概率能上进”的反事实判断。

**Planner / Replan**

- Round 1：`region [[530, 560]]`（错误时间窗）
- Round 2：`uniform full-video`（全片 fallback）

**关键证据**  
- Round 1 证明 `530-560s` 对应的是第一节末段，不是第二节初期；
- Round 2 在 `[1810, 1813]s` 找到最接近真实问题的突破回合；
- `[1824, 1829]s` 的慢动作回放确认：主防者是 `40号 Barnes`，`5号 Speights` 是协防封盖者。

**为什么这题我们不算正确**  
问题不再是系统“搜不到视频片段”，而是 **题干前提与原视频本身不完全一致**。系统已经把搜索和定位做对了，但最终只能基于最近似回合做反事实推断，因此这题仍然保留为未通过样例。

**可直接打开的产物**

- [`plan.initial.json`](avp/out/q1_to_q5_rerun_unified_20260321_180320/all_sample/sample_3/plan.initial.json)
- [`round_1 evidence.json`](avp/out/q1_to_q5_rerun_unified_20260321_180320/all_sample/sample_3/evidence/round_1/evidence.json)
- [`round_2 evidence.json`](avp/out/q1_to_q5_rerun_unified_20260321_180320/all_sample/sample_3/evidence/round_2/evidence.json)
- [`final_answer.json`](avp/out/q1_to_q5_rerun_unified_20260321_180320/all_sample/sample_3/final_answer.json)
- [`conversation_history.json`](avp/out/q1_to_q5_rerun_unified_20260321_180320/all_sample/sample_3/conversation_history.json)
- [`role_traces.jsonl`](avp/out/q1_to_q5_rerun_unified_20260321_180320/all_sample/sample_3/role_traces.jsonl)

</details>

### Q5 — 时序推理（我们判定：✅ 正确）

<details>
<summary><strong>展开查看 Q5 细节</strong></summary>

**问题**  
比赛开局时，俄克拉荷马城雷霆队以8-0领先金州勇士队。在这波进攻高潮中，雷霆队大概花了多少时间才取得这8分？

**参考答案**  
`约75秒到90秒之间`

**模型答案（原文）**  
> 在这波8-0的进攻高潮中，俄克拉荷马城雷霆队大约花了83秒才取得这8分。

**Planner**  
- 观察窗口：`[0s, 240s]`
- 轮数：`1`

**关键证据**  
- `[103, 187]s`：整段 `8-0` 开局高潮的完整时间跨度；
- `[129, 131]s`：第一次得分，比分到 `2-0`；
- `[156, 158]s`：第二次关键得分，比分到 `5-0`；
- `[185, 187]s`：第三次关键得分，比分到 `8-0`。

**为什么我们判定它正确**  
模型输出的是 **精确到秒的 `83 秒`**，而参考答案是 **区间形式 `75~90 秒`**。从语义上看，`83 秒` 完全落在参考区间内，而且和证据链严格一致。

**可直接打开的产物**

- [`plan.initial.json`](avp/out/q1_to_q5_rerun_unified_20260321_180320/all_sample/sample_4/plan.initial.json)
- [`evidence.json`](avp/out/q1_to_q5_rerun_unified_20260321_180320/all_sample/sample_4/evidence/round_1/evidence.json)
- [`final_answer.json`](avp/out/q1_to_q5_rerun_unified_20260321_180320/all_sample/sample_4/final_answer.json)
- [`conversation_history.json`](avp/out/q1_to_q5_rerun_unified_20260321_180320/all_sample/sample_4/conversation_history.json)
- [`role_traces.jsonl`](avp/out/q1_to_q5_rerun_unified_20260321_180320/all_sample/sample_4/role_traces.jsonl)

</details>

---

## 🧭 这个 Demo 里可以直接看什么？

如果你想真正“打开 Demo”而不是只看一张结果表，建议从下面这些文件开始：

### 顶层结果

- [`summary.json`](avp/out/q1_to_q5_rerun_unified_20260321_180320/summary.json)
- [`results.jsonl`](avp/out/q1_to_q5_rerun_unified_20260321_180320/results.jsonl)
- [`run.log`](avp/out/q1_to_q5_rerun_unified_20260321_180320/run.log)
- [`完整实验报告`](gemini_q1_to_q5_report.md)

### 每题样本目录结构

```text
all_sample/sample_k/
├── plan.initial.json
├── final_answer.json
├── conversation_history.json
├── history.jsonl
├── role_traces.jsonl
├── sample_metadata.json
└── evidence/
    └── round_*/
        └── evidence.json
```

### 这些文件分别表示什么？

- `plan.initial.json`：Planner 的初始观察计划
- `evidence/round_*/evidence.json`：Observer 抽出的结构化证据
- `final_answer.json`：Synthesizer 的最终答案和置信度
- `conversation_history.json`：多轮交互过程的完整轨迹
- `role_traces.jsonl`：更细粒度的角色级 prompt / response trace
- `history.jsonl`：样本级运行历史

---

## 🔁 AVP 是怎么工作的？

AVP 的核心不是“一次性读完整视频”，而是让系统自己做 **计划 -> 观察 -> 验证 -> 综合** 的循环。

```text
Planner -> Observer -> Reflector / Verifier -> Synthesizer
   ^                                              |
   +---------------- evidence insufficient -------+
```

在这个仓库里，这个循环特别适合用来展示：

- 长视频如何先看局部、再必要时 fallback 到全片；
- 不同 agent 角色如何围绕同一个问题协作；
- 为什么系统会答对、答偏，或者发现题干与视频不一致；
- 为什么开放式题目不应该只用字符串 exact-match 评价。

<div align="center">
  <img src="assets/vis.png" width="860" alt="AVP workflow figure">
</div>

---

## 🚀 快速开始

### 1）创建环境

```bash
conda create -n avp python=3.10 -y
conda activate avp
conda install -c conda-forge ffmpeg
pip install -r requirements.txt
```

### 2）配置 API

请根据 `avp/config.example.json` 准备配置文件。

如果你本地用 shell 文件保存 key，可以在运行前先：

```bash
source api_key_config.txt
```

### 3）运行单样本调试

```bash
python -m avp.eval_dataset \
  --ann avp/eval_anno/eval_lvbench.json \
  --out avp/out/debug_single \
  --config avp/config.example.json \
  --limit 1 \
  --max-turns 1 \
  --timeout 600
```

### 4）运行并行评测

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

或者直接使用：

```bash
bash avp/parrelel_run.sh
```

---

## 📂 关键入口文件

- `avp/main.py`：AVP 主控制器与四角色循环
- `avp/prompt.py`：Prompt 模板与结构化输出 schema
- `avp/video_utils.py`：视频裁剪、重编码、metadata 工具
- `avp/eval_dataset.py`：单进程数据集评测入口
- `avp/eval_parallel.py`：多进程并行评测入口
- `gemini_q1_to_q5_report.md`：这次五题 Demo 的完整实验报告

---

## 🙏 致谢

本仓库首页现在展示的是 **我们这份 fork 的 Demo 整理结果**，但原始研究工作与基础代码来自 AVP 原论文和原项目。

- 原项目：<https://github.com/SalesforceAIResearch/ActiveVideoPerception>
- 原论文：<https://arxiv.org/abs/2512.05774>

如果你使用原始 AVP 研究，请引用：

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
