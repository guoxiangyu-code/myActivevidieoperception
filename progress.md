# Progress Log: 方案A — Qwen3-Omni 迁移

> 前阶段 (方案B: Qwen2.5-VL) 已完成，tag qwen2.5 已推送。本日志记录 Qwen3-Omni 迁移进度。

---

## Session: 2026-03-29 — Qwen3-Omni DashScope 实施

### Phase 0: API 连通性验证 ✅
- ModelScope API: ❌ Qwen3-Omni 不在 ModelScope 模型列表，两个 token 均 401
- DashScope API: ✅ 文本/视频均可用，支持 base64 data URL 和 dashscope SDK 上传
- 可用模型: `qwen3-omni-flash`, `qwen-omni-turbo`
- 非流式也能用（与文档说法不同）
- `modalities=["text"]` 必需

### Phase 1-3: 代码改造 ✅
- `avp/config.py`: 默认模型→qwen3-omni-flash, base_url→DashScope, DASHSCOPE_API_KEY 优先
- `avp/qwen_client.py`:
  - 添加 `modalities=["text"]` 到所有 API 调用
  - 双路径视频处理: base64 (<14MB) / dashscope SDK (>14MB)
  - **混合模式**: 长视频 (>600s) 自动降级为帧模式，短片段用原生视频
  - 帧数上限 240 (DashScope 限制 250 data-uri)
  - 低分辨率音频保留 (None→"24k")
- `avp/config.qwen.example.json`: Qwen3-Omni 配置模板
- `api_key_config.txt`: 添加 DASHSCOPE_API_KEY
- `requirements.txt`: 添加 dashscope>=1.20.0

### Phase 4: Basketball 测试结果

#### 失败历史
| # | 方式 | 错误 |
|---|------|------|
| 1 | base64 内联 (53MB→71MB) | 超过 DashScope 20MB 字符串限制 |
| 2 | dashscope SDK 上传 | "The audio is too long" (30分钟视频) |
| 3 | 帧模式 512帧 | 超过 DashScope 250 data-uri 限制 |

#### 成功运行 (混合模式)
- 30分钟视频 → 自动使用帧模式 (240帧, 每帧~7.7s)
- 管道完整运行: Planner → Observer (1轮) → Verifier → Synthesizer
- 耗时: 98.4s
- **答案错误**: 预测 "0" 三分出手，正确答案 "2次"
- 原因: 帧间隔太大 (7.7s/帧)，无法追踪单次投篮动作

#### 三模型对比
| 模型 | 输入方式 | 答案 | 正确 | 耗时 | 轮次 |
|------|---------|------|------|------|------|
| Gemini 2.5 Pro | 原生视频+音频 | 2次 | ✅ | 164s | 2 |
| Qwen2.5-VL-72B (帧模式) | 32 JPEGs | 3次 | ❌ | 290s | 3 |
| **Qwen3-Omni-Flash (混合)** | **240帧 (降级)** | **0** | **❌** | **98.4s** | **1** |

### 关键发现
1. Qwen3-Omni 的音频时长限制使得 30 分钟视频无法原生处理
2. 混合模式工程上可行，但长视频的 Round 1 概览仍依赖帧模式
3. 对于 <10 分钟的目标片段 (Round 2+)，可以使用原生视频+音频
4. 本次测试 Verifier 在 Round 1 后就判定 sufficient (confidence=0.80)，未触发 Round 2 原生视频分析
5. 非流式调用实际可用，简化了实现

---

## 历史记录 (方案B: Qwen2.5-VL)

### Phase 1: 需求分析与接口规格确认
- **Status:** ✅ complete (含深度 GAP 分析)
- **Started:** 2026-03-29 04:41

- Actions taken:
  - 阅读 方案.md 全文（471→619 行），定位方案B 描述（行 601-605）
  - 使用 explore agent 深度分析 GeminiClient 类完整 API（6 个核心方法 + 7 个属性）
  - 使用 explore agent 分析 config.py、prompt.py、video_utils.py、eval_dataset.py、eval_parallel.py
  - 使用 explore agent 确认 WatchConfig/PlanSpec/Evidence 等数据类型
  - 使用 explore agent 查找 qwen_batch_eval.py/qwen_first_question_demo.py（确认不存在）
  - 确认所有 Gemini 专有类型：Part, Blob, VideoMetadata, GenerateContentConfig, genai.Client
  - 确认 3 种 API 调用场景：Planning（纯文本）、Video Inference（文本+视频）、Synthesis（纯文本）
  - 确认 3 条视频传入路径：Region+多区域、Region+单区域、Uniform
  - 确认 FPS 钳制逻辑从服务端→客户端的迁移方案
  - 确认 media_resolution → 帧 resize 尺寸的映射关系
  - 完成 findings.md 和 task_plan.md 编写
  - Web 研究 DashScope API 格式、Qwen2.5-VL MRoPE 时序编码
  - 确认 API 凭证：QWEN_API_KEY + yunwu.ai（与 Gemini 共用 base_url）
  - 确认视频输入双模式策略：video_url（优先）+ frames（回退）
  - **深度 GAP 分析**（4 个并行 explore agent，逐行分析全部 4 个核心方法 + Controller/Planner/Observer/Reflector）
  - 发现 10 个关键 GAP:
    1. ⚠️ Reflector 直接访问 client.client.models.generate_content()（行 1983）
    2. store.append_role_trace() 角色追踪
    3. _apply_temporal_plan_guards() 在 plan() 后调用
    4. _normalize_key_evidence_to_canonical_timebase() 在 evidence 后调用
    5. normalize_final_answer_output() 在 synthesize 后调用
    6. round_intervals_full_seconds() 时间戳取整
    7. media_inputs 元数据构建
    8. contents = [prompt] + parts 消息组装差异
    9. GenerateContentConfig 无 Qwen 等价
    10. Uniform ≥900s 重编码参数
  - 更新 findings.md 和 task_plan.md 加入所有 GAP 修复方案

- Files created/modified:
  - task_plan.md (created → updated x2) — 7 阶段实施计划 + GAP 修复
  - findings.md (created → updated x2) — 研究发现 + 10 个 GAP 分析
  - progress.md (created → updated x2) — 本文件

### Phase 2: AVPConfig 扩展
- **Status:** pending
- Actions taken:
  -

### Phase 3: 视频→消息转换模块
- **Status:** pending
- Actions taken:
  -

### Phase 4: QwenClient 核心类实现
- **Status:** pending
- Actions taken:
  -

### Phase 5: 工厂模式与 eval 管线集成
- **Status:** pending
- Actions taken:
  -

### Phase 6: 测试与验证
- **Status:** pending
- Actions taken:
  -

### Phase 7: 文档与交付
- **Status:** pending
- Actions taken:
  -

## 5-Question Reboot Check
| Question | Answer |
|----------|--------|
| Where am I? | Phase 1 ✅ complete → Phase 2 待开始 |
| Where am I going? | Phase 2 (config) → 3 (video module) → 4 (QwenClient) → 5 (factory) → 6 (test) → 7 (docs) |
| What's the goal? | QwenClient 适配层：通过 yunwu.ai OpenAI 兼容 API 使用 qwen2.5-vl-72b-instruct |
| What have I learned? | GeminiClient 完整接口、3 种调用场景、3 条视频路径、MRoPE 时序编码、双模式视频输入策略 |
| What have I done? | 完成需求分析、确认 API 凭证/端点、更新所有规划文件 |
- Files created/modified:
  -

### Phase 3: 帧提取模块实现
- **Status:** pending
- Actions taken:
  -
- Files created/modified:
  -

### Phase 4: QwenClient 核心类实现
- **Status:** pending
- Actions taken:
  -
- Files created/modified:
  -

### Phase 5: 工厂模式与 eval 管线集成
- **Status:** pending
- Actions taken:
  -
- Files created/modified:
  -

### Phase 6: FPS/分辨率控制适配
- **Status:** pending
- Actions taken:
  -
- Files created/modified:
  -

### Phase 7: 测试与验证
- **Status:** pending
- Actions taken:
  -
- Files created/modified:
  -

### Phase 8: 文档与交付
- **Status:** pending
- Actions taken:
  -
- Files created/modified:
  -

## Test Results
| Test | Input | Expected | Actual | Status |
|------|-------|----------|--------|--------|
| （待 Phase 7 填写） | | | | |

## Error Log
| Timestamp | Error | Attempt | Resolution |
|-----------|-------|---------|------------|
| （暂无） | | | |

## 5-Question Reboot Check
| Question | Answer |
|----------|--------|
| Where am I? | Phase 1 — 需求分析与接口规格确认 (in_progress) |
| Where am I going? | Phase 2-8: Config 扩展 → 帧提取 → QwenClient → 集成 → FPS适配 → 测试 → 文档 |
| What's the goal? | 写一个 QwenClient 适配层替代 GeminiClient，使 AthenaQA 支持千问 VL 模型 |
| What have I learned? | GeminiClient 有 6 个核心方法需镜像；3 种 API 调用场景；prompt.py 100% 可复用；video_utils.py 100% 可复用 |
| What have I done? | 完成完整代码分析，创建了 task_plan.md + findings.md + progress.md |

---
*Update after completing each phase or encountering errors*
