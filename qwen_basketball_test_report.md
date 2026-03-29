# QwenClient 集成测试报告 — Basketball_Full_001_1

## 一、测试概要

| 项目 | 值 |
|------|-----|
| **测试时间** | 2026-03-29T06:57 - 07:02 UTC |
| **数据集** | basketball_q2_only.json |
| **视频** | Basketball_Full_001_1.mp4 (496MB, 1839.4秒, 约30分钟) |
| **问题** | 比赛开始后，俄克拉荷马城雷霆队在得到前8分的过程中，总共完成了多少次三分出手？ |
| **标准答案** | 2次 (COT: 02:05上篮2分, 02:37三分, 03:00三分) |
| **后端** | qwen2.5-vl-72b-instruct via yunwu.ai OpenAI兼容代理 |
| **视频模式** | frames (image_url blocks) |
| **max_frame** | 32 (所有分辨率档位) |
| **max_rounds** | 3 |
| **总耗时** | 290.1秒 |
| **最终答案** | 3次 (Confidence: 0.80) — 错误 |

---

## 二、关键发现：yunwu.ai 代理不支持 video_url

在首次运行中发现 yunwu.ai 代理对 video_url 类型的 content block 返回 429 错误。

| 模态 | 结果 | 延迟 |
|------|------|------|
| 纯文本 | 成功 | 1.5s |
| image_url (单图) | 成功 | 1.1s |
| image_url (3帧) | 成功 | 3.0s |
| video_url (5KB视频) | 429错误 | 20.4s |
| video_url (10.6MB视频) | 超时 | >900s |

**结论：必须使用 frames 模式（qwen_video_mode: "frames"）**

---

## 三、各 Agent 交互时序图

```
时间线 (UTC)                Agent          操作
----------------------------------------------------------------------
06:57:45  -- PLANNER ------  接收 query + video_meta (duration=1839.4s)
                             temporal_hint: "02:05, 02:37, 03:00" (~55s span)
                             调用 _call_text_api(plan_replan_model, prompt)
                             prompt 长度: 11,880 chars
                             响应长度: 851 chars
                             解析为 PlanSpec:
                               load_mode=region, fps=2.0, medium
                               regions=[(125.0, 180.0)]
                             _apply_temporal_plan_guards() 校验通过
                             role_trace: "planner", round=0

          -- CONTROLLER ---  Round 1/3 开始

07:00:??  -- OBSERVER -----  接收 plan -> 创建视频片段
                             ffmpeg clip: 125.0s-180.0s -> 16.88MB
                             FPS 钳制: 2.0 -> 0.58 (max 32 frames)
                             OpenCV 提取 32 帧 -> 32x image_url blocks
                             调用 _call_video_api(execute_model, prompt, 32 images)
                             prompt 长度: 5,192 chars
                             *** API 调用耗时 ~260秒 ***
07:02:13                     响应长度: 594 chars
                             parse_json_response -> validate_against_schema OK
                             key_evidence: 3 items
                             _normalize_key_evidence_to_canonical_timebase()
                             round_intervals_full_seconds() 应用
                             Evidence 写入 round_1/evidence.json
                             role_trace: "observer", round=1

07:02:13  -- REFLECTOR ----  验证证据充分性 (启发式,无LLM调用)
                             total evidence items: 3
                             unique regions after dedup: 2
                             query_confidence: 0.80
                             判定: SUFFICIENT
                             role_trace: "reflector", round=1 (25 chars: heuristic)

07:02:13  -- SYNTHESIZER --  综合最终答案
                             调用 _call_text_api(plan_replan_model, prompt)
                             prompt 长度: 2,645 chars
07:02:23                     响应长度: 278 chars
                             normalize_final_answer_output() 处理
                             最终答案: "3次三分出手"
                             role_trace: "synthesizer", round=1

07:02:23  -- CONTROLLER ---  流程结束, 写入 final_answer.json
```

---

## 四、各 Agent 详细输入输出

### 4.1 Planner (Round 0)

**输入**:
- Query: "比赛开始后，俄克拉荷马城雷霆队在得到前8分的过程中，总共完成了多少次三分出手？"
- Video duration: 1839.37s
- temporal_hint_summary: "Dataset-provided reference timings from cot_reference: 02:05, 02:37, 03:00. The referenced event sequence spans about 55 seconds..."

**输出** (结构化JSON):
```json
{
  "reasoning": "The query asks for a factual count of three-point attempts during the first 8 points scored by the Oklahoma City Thunder. Given the temporal hints and the need to analyze a specific event sequence, a region analysis with a reasonable sampling rate is appropriate.",
  "steps": [{
    "step_id": "1",
    "description": "Analyze the video segment around the referenced timings...",
    "sub_query": "比赛开始后...",
    "load_mode": "region",
    "fps": 2.0,
    "spatial_token_rate": "medium",
    "regions": [[125.0, 180.0]]
  }],
  "completion_criteria": "Observation complete when the specified video segment is analyzed..."
}
```

**分析**: Planner 基于 temporal_hint (02:05=125s, 03:00=180s) 正确定位了时间范围 [125s, 180s]。选择 region 模式 + 2fps + medium 分辨率是合理的。

---

### 4.2 Observer (Round 1)

**视频处理链路**:
1. `create_video_clip()`: 从原始 496MB 视频裁剪 125s-180s -> 16.88MB MP4
2. `create_video_part()` (frames模式):
   - FPS 钳制: requested 2.0 -> effective 0.58 (32帧/55秒)
   - OpenCV 提取 32 帧 JPEG (quality=85)
   - 每帧编码为 `data:image/jpeg;base64,...`
3. `_call_video_api()`: 发送 32x image_url + 1x text block

**media_inputs 元数据**:
```json
{
  "input_type": "clip",
  "clip_path": ".../step_1_region_0_125000_180000.mp4",
  "absolute_start_sec": 125.0,
  "absolute_end_sec": 180.0,
  "clip_time_base": "clip_local_seconds"
}
```

**模型返回** (594 chars):
```json
{
  "detailed_response": "在比赛开始后的这段时间内，俄克拉荷马城雷霆队在得到前8分的过程中，总共完成了3次三分出手。这些三分球分别由不同的球员投出，并且都成功命中。",
  "key_evidence": [
    {"timestamp_start": 125.0, "timestamp_end": 140.0, "description": "第一次三分出手并命中"},
    {"timestamp_start": 145.0, "timestamp_end": 160.0, "description": "第二次三分出手并命中"},
    {"timestamp_start": 170.0, "timestamp_end": 180.0, "description": "第三次三分出手并命中"}
  ],
  "reasoning": "通过仔细观察视频片段，可以看到雷霆队在得到前8分的过程中，有三次明显的三分出手动作..."
}
```

**时间轴规范化**:
- `_normalize_key_evidence_to_canonical_timebase()`: applied=false (单clip, 时间已是绝对值)
- `round_intervals_full_seconds()`: 去重合并后 -> 2 个唯一区间 [125,160], [170,180]

---

### 4.3 Reflector (Round 1)

**模式**: 启发式验证（未调用LLM）

**评估结果**:
- Total evidence items: 3
- Unique regions (after dedup/merge): 2
- Query confidence: 0.80
- **决策: SUFFICIENT -> 跳过后续轮次，直接合成答案**

---

### 4.4 Synthesizer (Round 1)

**输入**:
- original_query + all_evidence (3 items from Round 1)
- Prompt 长度: 2,645 chars
- Open-ended format (无MCQ选项)

**模型返回** (278 chars):
```json
{
  "answer": "俄克拉荷马城雷霆队在得到前8分的过程中，总共完成了3次三分出手。",
  "key_timestamps": [125.0, 140.0, 145.0, 160.0, 170.0, 180.0],
  "confidence": 0.8,
  "evidence_summary": "根据观察...分别在125.0s-140.0s、145.0s-160.0s和170.0s-180.0s这三个时间段内完成了三次三分出手..."
}
```

---

## 五、错误分析

### 5.1 答案偏差原因

| 因素 | 分析 |
|------|------|
| **帧采样不足** | 32帧覆盖55秒，每帧间隔约1.7秒。篮球三分出手动作持续不到1秒，可能被两帧之间遗漏 |
| **首分上篮被误判** | 标准答案：02:05 是上篮得2分，不是三分。但模型将区间内的某个得分动作误判为三分出手 |
| **帧模式信息损失** | 对比 Gemini 原生视频输入（连续帧+音频），frames 模式丢失了帧间运动信息和解说音频 |
| **缺乏投篮类型提示** | 提示词未明确要求模型区分"三分出手"和其他得分方式（上篮、罚球等） |

### 5.2 时间轴一致性验证

时间轴在各 agent 之间**保持一致**：
- Planner 输出 regions 使用绝对时间 [125.0, 180.0]
- Observer 的 media_inputs 记录 absolute_start_sec=125.0, absolute_end_sec=180.0
- Evidence key_evidence 的 timestamp_start/end 已通过 round_intervals_full_seconds() 规范化
- Synthesizer 引用的 key_timestamps 与 evidence 一致

---

## 六、性能指标

| 阶段 | 耗时 | API调用 | Token估算 |
|------|------|---------|----------|
| Planner | ~2s | 1x text | ~12K input / ~0.8K output |
| Observer (含ffmpeg+帧提取+API) | ~260s | 1x multimodal | ~5K text + 32 images / ~0.6K output |
| Reflector | <0.1s | 0 (heuristic) | -- |
| Synthesizer | ~10s | 1x text | ~2.6K input / ~0.3K output |
| **总计** | **290.1s** | **3x API calls** | -- |

---

## 七、输出文件清单

```
avp/out/qwen_basketball_test/
  results.jsonl                          # 评测结果
  summary.json                           # 总结 (accuracy=0.0)
  all_sample/sample_0/
    sample_metadata.json               # 样本元数据
    meta.json                          # 运行时元数据
    plan.initial.json                  # 初始计划
    history.jsonl                      # 事件时间线 (4 events)
    conversation_history.json          # 完整对话记录
    role_traces.jsonl                  # Agent角色追踪 (4 traces)
    final_answer.json                  # 最终答案
    evidence/round_1/evidence.json     # Round 1 证据
    temp_clips/
      step_1_region_0_125000_180000.mp4  # 裁剪的视频片段 (16.88MB)
```

---

## 八、改进建议

1. **提高帧率**: 对于动作密集的体育视频，32帧/55秒(约0.58fps)过低。建议 max_frame_medium 提升至 64-128
2. **多轮复查**: Reflector 在 confidence=0.80 时直接判定 SUFFICIENT，未触发 Round 2。对于需要计数的问题，可以考虑降低阈值
3. **解决 video_url 支持**: 如果能使用支持视频原生输入的 API 端点（如 DashScope 官方 API），可显著提升视频理解质量
4. **区分出手类型**: 提示词可以更具体地要求模型区分"三分出手"和其他得分方式（上篮、罚球等）
5. **增加音频分析**: Gemini 原生视频理解包含音频轨道（解说），frames 模式完全丢失了音频信息
