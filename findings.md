# Findings & Decisions: 方案B — QwenClient 适配层（API 模式）

## Requirements
- 保持 AthenaQA 上层逻辑（Controller/Planner/Observer/Reflector）不变
- 编写 `QwenClient` 适配层替代 `GeminiClient`
- 通过 **OpenAI 兼容 API**（yunwu.ai 代理）使用 **qwen2.5-vl-72b-instruct**
- 兼容现有的 eval_dataset.py 和 eval_parallel.py 评测管线
- 保持向后兼容（默认仍为 Gemini 后端）

## API 凭证与端点
```
QWEN_API_KEY = "sk-77x5ODCEevTIob1HdRbF4zpZ4iK1yvwiUV1Ch95VIn7kFSds"
QWEN_BASE_URL = "https://yunwu.ai"  (与 Gemini 共用 base_url)
MODEL = "qwen2.5-vl-72b-instruct"
SDK = openai Python package
```

## Research Findings

### 1. GeminiClient 公共 API（需要在 QwenClient 中镜像的接口）

**6 个核心方法 + 7 个属性/状态变量：**

| 方法 | 行号 (main.py) | 签名 | 返回值 | Qwen 适配策略 |
|------|----------------|------|--------|--------------|
| `__init__` | 636 | `(model, plan_replan_model, execute_model, project, location, api_key, base_url, debug, max_frame_*, prefer_compressed, keep_temp_clips)` | None | 去掉 project/location，加 qwen_video_mode |
| `initialize_client` | 672 | `()` | None | 创建 `openai.OpenAI(api_key, base_url)` |
| `create_video_part` | 722 | `(video_path, fps, start_offset, end_offset, media_resolution, duration_sec)` | `Part` | → 返回 `List[dict]`（OpenAI content 块） |
| `plan` | 844 | `(query, video_meta, prior, options, store, round_id)` | `PlanSpec` | `client.chat.completions.create()` |
| `infer_on_video` | 1015 | `(video_path, duration_sec, sub_query, context, start_sec, end_sec, watch_cfg, step_id, ...)` | `Evidence` | 三条路径保持，video 输入格式改为 OpenAI messages |
| `synthesize_final_answer` | 1654 | `(plan, bb, store, round_id)` | `Dict` | `client.chat.completions.create()` |

**Controller 访问的属性/状态（必须保留）：**
- `client.prefer_compressed` — bool
- `client.debug` — bool
- `client.model` / `client.plan_replan_model` / `client.execute_model` — str
- `client.keep_temp_clips` — bool
- `client.created_clips` — List[str]（需要被 Controller 重置和读取）
- `client.temp_clips_dir` — str（需要被 Controller 设置）

### 2. Gemini → Qwen 类型替换对照表

| Gemini 类型 | 用途 | Qwen 替代 |
|-------------|------|-----------|
| `genai.Client` | API 客户端 | `openai.OpenAI(api_key, base_url)` |
| `Part(inlineData=Blob, videoMetadata=VM)` | 视频内容单元 | `List[dict]` (OpenAI content 块) |
| `Blob(mime_type, data)` | 二进制视频数据 | base64 编码 data URI |
| `VideoMetadata(fps, startOffset, endOffset)` | 服务端采样控制 | 客户端 OpenCV 帧提取参数 |
| `GenerateContentConfig(media_resolution)` | 空间分辨率 | 帧 resize 尺寸参数 |
| `client.models.generate_content(model, contents)` | API 调用 | `client.chat.completions.create(model, messages)` |
| `response.text` | 响应文本 | `response.choices[0].message.content` |

### 3. OpenAI 消息格式（Qwen 路径）

#### 纯文本调用（Planning / Synthesis）
```python
messages = [{"role": "user", "content": prompt_text}]
response = client.chat.completions.create(model=model, messages=messages)
text = response.choices[0].message.content
```

#### 视频推理调用 — video_url 模式（优先）
```python
import base64
with open(video_path, "rb") as f:
    b64 = base64.b64encode(f.read()).decode()
data_uri = f"data:{mime_type};base64,{b64}"

messages = [{
    "role": "user",
    "content": [
        {"type": "video_url", "video_url": {"url": data_uri}},
        {"type": "text", "text": prompt}
    ]
}]
```

#### 视频推理调用 — frames 模式（回退）
```python
frames_b64 = extract_frames_from_video(video_path, fps, start, end, max_frames, resize)
content = []
for b64 in frames_b64:
    content.append({
        "type": "image_url",
        "image_url": {"url": f"data:image/jpeg;base64,{b64}"}
    })
content.append({"type": "text", "text": prompt})

messages = [{"role": "user", "content": content}]
```

### 4. infer_on_video() 的三条视频传入路径

| 路径 | 触发条件 | Gemini 做法 | Qwen 做法 |
|------|----------|------------|-----------|
| **Region + 多区域** | `load_mode=="region"` & 多 regions | ffmpeg 裁剪 N 个 clip → N 个 Part(Blob) | ffmpeg 裁剪 N 个 clip → 每 clip → video_url/frames content → 组合 |
| **Region + 单区域** | `load_mode=="region"` & 1 region | ffmpeg 裁剪 1 个 clip → 1 个 Part(Blob) | ffmpeg 裁剪 1 个 clip → video_url/frames content |
| **Uniform** | `load_mode=="uniform"` | 整视频 + offset 元数据 (或 ≥900s 重编码) | video_url 整视频 / frames 整视频抽帧 (长视频先重编码) |

### 5. FPS 钳制逻辑

```
# 逻辑完全相同，只是执行位置从 Gemini 服务端 → Qwen 客户端 OpenCV
effective_fps = min(requested_fps, max_frame / duration_sec)
actual_frames = int(effective_fps * duration_sec)
```

| media_resolution | max_frame | resize_short_edge |
|------------------|-----------|--------------------|
| low | 512 | 480px |
| medium | 128 | 640px |
| high | 128 | 854px |

### 6. Qwen2.5-VL MRoPE 时序编码

**关键发现**: Qwen2.5-VL 使用 **MRoPE (Multimodal Rotary Position Embedding)** 编码帧的绝对时间位置。
- 即使以独立帧图片输入，模型仍会通过 T 维位置 ID 理解帧间时序关系
- 这显著减轻了 "帧间信息丢失" 的担忧（从 Gemini→其他模型的严重问题 降级为 中低级问题）
- **video_url 模式更优**：模型可自主进行动态 FPS 采样，比客户端固定 FPS 抽帧更精确
- frames 模式仍然有效，但丢失了模型自主采样的优势

### 7. 模型无关的组件（不需修改）

| 组件 | 文件 | 理由 |
|------|------|------|
| PromptManager 所有方法 | prompt.py | 纯文本 prompt，无模型特异性 |
| parse_json_response() | prompt.py:1052 | 通用 JSON 解析 |
| validate_against_schema() | prompt.py:1124 | 通用 schema 验证 |
| WatchConfig / PlanSpec / Evidence / Blackboard | main.py:341-439 | 通用数据结构 |
| Controller / Planner / Observer / Reflector | main.py:2192+ | 通过 `self.client` 使用鸭子类型 |
| 所有 video_utils.py 已有函数 | video_utils.py | ffmpeg/元数据函数无模型依赖 |
| eval_parallel.py | eval_parallel.py | 纯子进程调度 |
| 所有 JSON schemas | prompt.py:17-130 | 通用 JSON schema |

### 8. 文件改动清单

**需修改的文件：**

| 文件 | 改动范围 | 改动描述 |
|------|----------|----------|
| `avp/config.py` | ~25 行新增 | 添加 `backend`, `qwen_api_key`, `qwen_base_url`, `qwen_model`, `qwen_video_mode` |
| `avp/video_utils.py` | ~80 行新增 | 添加 `extract_frames_from_video()`, `encode_video_to_base64()`, `build_qwen_video_content()` |
| `avp/eval_dataset.py` | ~20 行修改 | 使用 `create_client()` 工厂函数 |
| `avp/main.py` | ~5 行修改 | CLI 添加 --backend 选项 |
| `requirements.txt` | 1 行新增 | `openai>=1.0.0` |

**需新建的文件：**

| 文件 | 预估行数 | 描述 |
|------|----------|------|
| `avp/qwen_client.py` | ~550 行 | QwenClient 核心 + `create_client()` 工厂函数 |
| `avp/config.qwen.example.json` | ~20 行 | Qwen 后端示例配置 |

### 9. 风险与应对

| 风险 | 概率 | 影响 | 应对 |
|------|------|------|------|
| yunwu.ai 不支持 video_url 类型 | 中 | video模式失败 | 自动降级为 frames 模式 |
| base64 视频过大被 API 拒绝 (413) | 中 | 长视频传输失败 | 先 ffmpeg 裁剪/重编码压缩，或自动降级 frames |
| Qwen 结构化输出能力弱于 Gemini | 低-中 | JSON 解析失败率上升 | 复用 GeminiClient 3 级 fallback 解析逻辑 |
| OpenAI SDK 版本兼容性 | 低 | import 错误 | 限定 `openai>=1.0.0` |

## 🔴 GAP ANALYSIS: 当前计划 vs 实际 Gemini 实现

### Gap 1: Reflector 直接访问底层 client（严重 ⚠️）
**现状**: Reflector 在 `is_last_round=True` 时**绕过** GeminiClient 的公共方法，直接调用:
```python
self.client.client.models.generate_content(  # line 1983
    model=self.client.plan_replan_model,
    contents=prompt
)
```
**影响**: QwenClient 必须暴露一个 `.client` 属性，其行为足以让 Reflector 正常工作。
**方案A（推荐）**: 在 QwenClient 上提供一个轻量代理对象 `self.client`，它有 `.models.generate_content(model, contents)` 接口，内部转发到 OpenAI SDK。
**方案B**: 修改 Reflector（~3 行），改为调用 `self.client.generate_text(prompt)`。但这会修改 main.py 中的上层逻辑，违背方案B"上层不变"的原则。
**决策**: 采用方案A。创建 `_OpenAICompatProxy` 内部类模拟 `genai.Client` 接口。

### Gap 2: `store.append_role_trace()` 角色追踪
**现状**: GeminiClient 的 plan()、infer_on_video()、synthesize_final_answer() 都在 API 调用后记录 role trace:
```python
store.append_role_trace("planner", round_id, prompt, response_text)  # line 900
store.append_role_trace("observer", round_id, prompt, response_text)  # line 1405
store.append_role_trace("synthesizer", round_id, prompt, response_text)  # line 1697
```
Reflector 也记录:
```python
store.append_role_trace("reflector_synthesizer", round_id, prompt, response_text)  # line 1991
store.append_role_trace("reflector", round_id, ...)  # lines 2124, 2179
```
**影响**: QwenClient 必须在每个方法中实现相同的 role trace 调用。
**之前计划**: 未提及。
**修复**: 在 QwenClient 的 plan/infer_on_video/synthesize_final_answer 中增加相同的 store.append_role_trace 调用。

### Gap 3: `_apply_temporal_plan_guards()` 在 plan() 后调用
**现状**: GeminiClient.plan() 在解析 PlanSpec 后调用:
```python
plan = _apply_temporal_plan_guards(plan, query, video_meta, debug=self.debug)  # line 979
```
这是 main.py 的模块级函数（行 261），不是 GeminiClient 方法。
**影响**: QwenClient.plan() 必须在同一位置调用此函数。
**之前计划**: 未提及。
**修复**: 在 QwenClient.plan() 中 import 并调用 `_apply_temporal_plan_guards`。

### Gap 4: `_normalize_key_evidence_to_canonical_timebase()` 在 evidence 解析后调用
**现状**: GeminiClient.infer_on_video() 在解析 evidence 后调用:
```python
key_evidence, time_normalization = _normalize_key_evidence_to_canonical_timebase(
    key_evidence=key_evidence, media_inputs=media_inputs,
    duration_sec=duration_sec, debug=self.debug,
)  # line 1444
```
**影响**: QwenClient.infer_on_video() 必须调用此函数做时间基准归一化。
**之前计划**: 未提及。
**修复**: 在 QwenClient.infer_on_video() 中 import 并调用。

### Gap 5: `normalize_final_answer_output()` 在 synthesize 后调用
**现状**: GeminiClient.synthesize_final_answer() 调用:
```python
normalized = normalize_final_answer_output(
    answer_data=answer_data, response_text=response_text,
    query_confidence=query_confidence, options=options_list,
)  # line 1702
```
**影响**: QwenClient.synthesize_final_answer() 必须使用相同的归一化。
**之前计划**: 提到了复用，但未详细说明。
**修复**: 直接 import 并调用。

### Gap 6: `round_intervals_full_seconds()` 时间戳取整逻辑
**现状**: infer_on_video() 在 evidence 后还做了时间戳取整:
```python
rounded = round_intervals_full_seconds(raw_ranges, duration=duration_sec)  # line 1464
```
**影响**: QwenClient 必须实现相同的取整逻辑。
**之前计划**: 未提及。
**修复**: 直接 import 并复用 `round_intervals_full_seconds`（来自 video_utils.py）。

### Gap 7: `media_inputs` 元数据构建
**现状**: infer_on_video() 为每个 clip/视频构建详细的 `media_inputs` 列表（包含 input_type, clip_time_base, absolute_start/end 等），用于:
1. 传给 `PromptManager.get_inference_prompt()` 的 `media_inputs` 参数
2. 传给 `_normalize_key_evidence_to_canonical_timebase()` 做时间转换
3. 存入 Evidence.model_call 元数据
**影响**: QwenClient 必须构建完全相同的 media_inputs 结构。
**之前计划**: 未详细说明。
**修复**: 完全复制三条路径中的 media_inputs 构建逻辑。

### Gap 8: `contents = [prompt] + parts` 消息组装方式
**现状**: Gemini API 使用扁平列表: `[prompt_text, Part1, Part2, ...]`
**QwenClient**: OpenAI API 使用嵌套 messages:
```python
[{"role": "user", "content": [
    {"type": "text", "text": prompt},
    {"type": "video_url", "video_url": {"url": "data:..."}},  # 或 image_url
    ...
]}]
```
**影响**: 虽然 `infer_on_video()` 内部实现不同，但外部接口（参数和返回值）保持一致。
**修复**: QwenClient.infer_on_video() 内部将 parts 转换为 OpenAI content 块。

### Gap 9: `GenerateContentConfig(media_resolution=...)` 
**现状**: Gemini API 接受 `GenerateContentConfig(media_resolution="MEDIA_RESOLUTION_LOW")` 作为 API 级参数。
**QwenClient**: 没有等价的 API 参数。media_resolution 控制的是客户端帧 resize 尺寸。
**之前计划**: 已正确处理（low→480p, medium→640p, high→854p）。
**修复**: 无需额外修复，已在计划中。

### Gap 10: Uniform 模式 ≥900s 重编码参数
**现状**: 当 `duration_sec >= 900.0` 且 `load_mode == "uniform"` 时，使用 `create_reencoded_video_clip()` 进行压缩重编码:
- low: scale_width=480, bitrate=220k, fps=6.0, crf=32
- medium: scale_width=640, bitrate=320k, audio=32k, fps=8.0, crf=30
- high: scale_width=854, bitrate=550k, audio=48k, fps=10.0, crf=28
**影响**: QwenClient 也应在长视频时做重编码压缩。
- **video_url 模式**: 直接传重编码后的 clip（与 Gemini 一致）
- **frames 模式**: 从重编码 clip 抽帧（更高效，因为已压缩）
**之前计划**: 提到了 "长视频先重编码" 但未详述参数。
**修复**: 直接复用 `create_reencoded_video_clip()` 的调用方式。

## Technical Decisions (Updated)
| Decision | Rationale |
|----------|-----------|
| 仅 API 模式（不支持本地推理） | 用户明确选择 API；72B 本地推理不实际 |
| openai SDK | yunwu.ai 是 OpenAI 兼容代理，openai 包最标准 |
| 双模式视频输入 (video_url + frames) | video_url 保留 MRoPE 时序；frames 作通用回退 |
| 独立 `avp/qwen_client.py` | 不污染 GeminiClient，单一职责 |
| 工厂函数在 qwen_client.py | 避免额外小文件 |
| 复用 ffmpeg 裁剪 | 三条路径结构可直接移植 |
| 帧编码 JPEG + base64 | 压缩比高、OpenAI messages 原生支持 |
| **NEW**: `_OpenAICompatProxy` 模拟 genai.Client | 解决 Reflector 直接访问 client.client 问题，无需修改 main.py 上层代码 |
| **NEW**: 完全复用模块级辅助函数 | `_apply_temporal_plan_guards`, `_normalize_key_evidence_to_canonical_timebase`, `normalize_final_answer_output`, `round_intervals_full_seconds` 等 |
| **NEW**: 完全复制 media_inputs 构建逻辑 | 保证 prompt 和 evidence 时间归一化与 Gemini 路径一致 |
| **NEW**: 角色追踪 (role trace) | 每个 API 调用后调用 store.append_role_trace()，与 Gemini 行为一致 |

## Issues Encountered
| Issue | Resolution |
|-------|------------|
| qwen_batch_eval.py / qwen_first_question_demo.py 不存在 | 从零实现 QwenClient |
| api_key_config.txt 无 QWEN_BASE_URL | 用户确认与 Gemini 共用 yunwu.ai |
| **NEW**: Reflector 直接访问 client.client.models | 创建 `_OpenAICompatProxy` 代理对象 |
| **NEW**: 10 处 gap 需要在 QwenClient 中精确对齐 | 已全部记录并制定修复方案 |

## Resources
- `avp/main.py` GeminiClient: 行 636-1711
- `avp/main.py` Reflector 直接 API 调用: 行 1980-1986
- `avp/main.py` 模块级辅助函数: `normalize_final_answer_output` (82), `_normalize_key_evidence_to_canonical_timebase` (173), `_apply_temporal_plan_guards` (261)
- `avp/config.py` AVPConfig: 行 33-122
- `avp/prompt.py` PromptManager: 行 137-1138
- `avp/video_utils.py`: 行 1-887, `round_intervals_full_seconds`
- `avp/eval_dataset.py` GeminiClient 实例化: 行 278-295
- OpenAI Python SDK: https://github.com/openai/openai-python
- Qwen2.5-VL: https://huggingface.co/Qwen/Qwen2.5-VL-72B-Instruct

---
*Last updated: 深度 GAP 分析完成 (10 个 gap 已识别并制定修复方案)*
