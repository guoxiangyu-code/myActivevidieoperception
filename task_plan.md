# Task Plan: 方案A — Qwen3-Omni 迁移（DashScope 原生视频+音频）

## Background

方案B (Qwen2.5-VL frames模式) 已实现并测试，但因帧采样丢失了关键时序信息（误判上篮为三分球），
效果不及 Gemini 原生视频。本方案迁移到 **Qwen3-Omni**，利用 DashScope 官方 API 的原生视频+音频
理解能力，尽可能贴近 Gemini 的工作方式。

## Goal

在现有 QwenClient 基础上适配 **Qwen3-Omni** 模型，通过 DashScope 官方 API 发送
**原生视频文件**（含音频），使视频理解能力接近 Gemini 2.5 Pro。

核心改动:
1. 流式响应 (`stream=True` 强制)
2. 视频以 `video_url` (文件路径/URL) 而非 base64 发送
3. DashScope 端点替代 yunwu.ai 代理
4. 音频原生保留

## 技术栈决策

| 维度 | 决定 | 理由 |
|------|------|------|
| SDK | `openai` Python 包 (继续使用) | DashScope 提供 OpenAI 兼容端点 |
| API 端点 | `https://dashscope.aliyuncs.com/compatible-mode/v1` | 阿里云官方，支持 video_url |
| API Key | `DASHSCOPE_API_KEY`（或复用 `QWEN_API_KEY`） | 需要验证 |
| 模型 | `qwen3-omni` / `qwen3-omni-flash` | 原生视频+音频理解 |
| 视频输入 | `video_url` (本地 file:// 或 HTTP URL) | 原生视频，保留全部时序+音频 |
| 流式 | `stream=True` (强制) | Qwen3-Omni API 要求 |
| 输出模态 | `modalities=["text"]` | 只需文本，不需语音合成 |

## Current Phase
Phase 0 (待用户确认后实施)

## Phases

### Phase 0: API 连通性验证 [pending]
**目标**: 确认 DashScope 端点、API Key、Qwen3-Omni 模型可用。
- [ ] 0a. 检查 `api_key_config.txt` 中的 QWEN_API_KEY 是否能直连 DashScope
- [ ] 0b. 用 curl 测试 DashScope 端点连通性
- [ ] 0c. 用简单文本请求测试 Qwen3-Omni 模型是否可用 (stream=True)
- [ ] 0d. 用短视频 (< 5MB) 测试 video_url 模式是否可用
- [ ] 0e. 测试本地文件路径 `file:///path/to/video.mp4` 是否被支持
- [ ] 0f. 如果 openai SDK + file:// 不工作，评估是否需要 `dashscope` SDK
- **Status:** pending

### Phase 1: 流式响应重构 [pending]
**目标**: 使 `_call_text_api()` 和 `_call_video_api()` 支持 stream=True。

改动文件: `avp/qwen_client.py`

- [ ] 1a. 重构 `_call_text_api()`:
  ```python
  # 当前 (L177-185): 非流式
  resp = self._openai_client.chat.completions.create(model=model, messages=messages)
  return resp.choices[0].message.content
  
  # 目标: 流式
  stream = self._openai_client.chat.completions.create(
      model=model, messages=messages, stream=True, modalities=["text"]
  )
  full_text = ""
  for chunk in stream:
      if chunk.choices and chunk.choices[0].delta.content:
          full_text += chunk.choices[0].delta.content
  return full_text
  ```

- [ ] 1b. 重构 `_call_video_api()`:
  ```python
  # 当前 (L187-196): 非流式
  resp = self._openai_client.chat.completions.create(model=model, messages=messages)
  return resp.choices[0].message.content
  
  # 目标: 流式 + modalities
  stream = self._openai_client.chat.completions.create(
      model=model, messages=messages, stream=True, modalities=["text"]
  )
  full_text = ""
  for chunk in stream:
      if chunk.choices and chunk.choices[0].delta.content:
          full_text += chunk.choices[0].delta.content
  return full_text
  ```

- [ ] 1c. 统一错误处理: 流式调用的异常可能在迭代中抛出，需 try/except 包裹 chunk loop
- [ ] 1d. 添加超时/重试: 流式调用可能更慢（逐 chunk），保持现有 retry 逻辑
- [ ] 1e. 更新 `_OpenAICompatProxy.generate_content()` 同样使用流式 (Reflector 调用路径)
- **Status:** pending

### Phase 2: 视频输入路径重构 [pending]
**目标**: 从 base64 data URL / frames 切换到 video_url 模式。

改动文件: `avp/qwen_client.py`

- [ ] 2a. 新增 `_create_video_url_part()` 方法:
  ```python
  def _create_video_url_part(self, video_path: str) -> List[dict]:
      """使用 video_url 模式发送原生视频文件"""
      # 方案 A: 直接用 file:// 路径 (需要 DashScope 支持)
      url = f"file://{os.path.abspath(video_path)}"
      # 方案 B: 如果 file:// 不支持，用 base64 data URL (现有逻辑)
      return [{"type": "video_url", "video_url": {"url": url}}]
  ```

- [ ] 2b. 修改 `create_video_part()` (L246-381):
  - 新增 `video_mode == "native"` 路径: 调用 `_create_video_url_part()`
  - 保留 `"video"` (base64)、`"frames"` (帧提取)、`"auto"` 作为回退
  - 默认 mode 从 `"frames"` 改为 `"native"`

- [ ] 2c. 确保音频保留:
  - 检查 `create_video_clip()` 的 `-c copy` 默认保留音频 ✅
  - 检查 `create_reencoded_video_clip()` 的 audio_bitrate 逻辑:
    - low: `-an` (无音频) → **改为 `-c:a aac -b:a 32k`** (保留低码率音频)
    - medium/high: 已有 `32k`/`48k` ✅
  - **关键改动**: 低分辨率模式不再剥离音频（Qwen3-Omni 需要音频理解）

- [ ] 2d. 保留帧提取作为 fallback:
  - 如果 video_url 失败（API 错误、文件太大），自动降级为 frames 模式
  - 利用现有 auto 模式逻辑

- **Status:** pending
- **依赖:** Phase 0 (确认 video_url 工作方式)

### Phase 3: 配置与端点更新 [pending]
**目标**: 添加 DashScope 相关配置、更新默认值。

改动文件: `avp/config.py`, `avp/config.qwen.example.json`

- [ ] 3a. `avp/config.py` 新增/修改字段:
  ```python
  qwen_model: str = "qwen3-omni"         # 默认改为 qwen3-omni
  qwen_base_url: str = ""                 # 默认空，运行时 fallback 到 DashScope 端点
  qwen_video_mode: str = "native"         # 默认改为 native (原生视频)
  ```

- [ ] 3b. 环境变量优先级:
  ```
  DASHSCOPE_API_KEY > QWEN_API_KEY > config.qwen_api_key
  DASHSCOPE_BASE_URL > QWEN_BASE_URL > config.qwen_base_url > "https://dashscope.aliyuncs.com/compatible-mode/v1"
  ```

- [ ] 3c. 更新 `avp/config.qwen.example.json`:
  ```json
  {
    "backend": "qwen",
    "qwen_model": "qwen3-omni",
    "qwen_plan_model": "qwen3-omni",
    "qwen_video_mode": "native",
    "qwen_base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "prefer_compressed": true,
    "max_frame_low": 512,
    "fps": 1.0
  }
  ```

- [ ] 3d. QwenClient.__init__() 更新默认 base_url 逻辑
- **Status:** pending

### Phase 4: 集成测试 [pending]
**目标**: 在 basketball_q2_only.json 上测试，比较 Gemini/Qwen2.5-VL/Qwen3-Omni 效果。

- [ ] 4a. 运行测试:
  ```bash
  source api_key_config.txt
  python -m avp.eval_dataset \
    --ann dataset/SportsTime/basketball_q2_only.json \
    --out avp/out/qwen3_omni_basketball_test \
    --config avp/config.qwen.example.json \
    --limit 1 --max-turns 3 --timeout 1200
  ```

- [ ] 4b. 检查输出: results.jsonl, summary.json, all_sample/sample_0/
- [ ] 4c. 分析各 agent 交互: Planner → Observer → Reflector → 最终答案
- [ ] 4d. 写详细测试报告，与 Gemini/Qwen2.5-VL 结果对比

  | 模型 | 输入方式 | 答案 | 正确 | 耗时 |
  |------|---------|------|------|------|
  | Qwen2.5-VL (frames) | 32 JPEGs | 3次 | ❌ | 290s |
  | Gemini 2.5 Pro (native) | 视频+音频 | 2次 | ✅ | 164s |
  | **Qwen3-Omni (native)** | **视频+音频** | **?** | **?** | **?** |

- **Status:** pending
- **依赖:** Phase 1, 2, 3

### Phase 5: 文档与提交 [pending]
- [ ] 5a. 更新 README.md (移除/修改 Qwen2.5 失败提示)
- [ ] 5b. 更新 copilot_change_log.jsonl
- [ ] 5c. Git commit + tag `qwen3-omni`
- [ ] 5d. Push to remote
- **Status:** pending
- **依赖:** Phase 4

## GeminiClient vs QwenClient 对照 (指导改动方向)

### 视频发送方式对比

| 维度 | Gemini | QwenClient (当前) | QwenClient (目标) |
|------|--------|-------------------|-------------------|
| 数据格式 | `Part(inlineData=Blob(data=bytes))` | base64 data URL / JPEG frames | `video_url` (file://path) |
| 音频 | Blob 内含音频 | ❌ 帧模式丢失 | ✅ 原生视频含音频 |
| 元数据 | `VideoMetadata(fps, startOffset, endOffset)` | 无 (帧采样时隐含) | 无 (服务端处理) |
| 分辨率控制 | `media_resolution="MEDIA_RESOLUTION_LOW"` | resize_short_edge 控制帧大小 | 服务端自动处理 |
| 多视频 | `contents=[prompt, Part1, Part2, ...]` | `messages[0].content=[...blocks...]` | 同上 |

### API 调用方式对比

| 维度 | Gemini | QwenClient (当前) | QwenClient (目标) |
|------|--------|-------------------|-------------------|
| 调用 | `client.models.generate_content()` | `openai.chat.completions.create()` | 同左, +stream=True |
| 流式 | 非流式 | 非流式 | **流式** (强制) |
| 响应 | `resp.text` | `resp.choices[0].message.content` | 累积 stream chunks |
| 配置 | `GenerateContentConfig(media_resolution=...)` | 无 | `modalities=["text"]` |

### 需要修改的代码路径

| 方法 | 改动范围 | 原因 |
|------|---------|------|
| `_call_text_api()` (L177) | **中等** | 添加 stream=True + chunk 累积 |
| `_call_video_api()` (L187) | **中等** | 添加 stream=True + modalities + chunk 累积 |
| `create_video_part()` (L246) | **大** | 新增 "native" 视频路径 (video_url) |
| `_OpenAICompatProxy.generate_content()` (L65) | **小** | 同步流式调用 |
| `__init__()` (L97) | **小** | 默认 base_url → DashScope |
| `initialize_client()` (L156) | **小** | 确认 base_url 默认值 |
| `infer_on_video()` (L543) | **可能不动** | 三条路径结构不变，只是 create_video_part 输出格式变了 |
| `plan()` (L397) | **可能不动** | 纯文本调用，但需流式 |
| `synthesize_final_answer()` (L966) | **可能不动** | 纯文本调用，但需流式 |

### 不需要修改的文件

- `avp/prompt.py` — 所有 prompt 是纯文本，模型无关
- `avp/main.py` — GeminiClient 不受影响，QwenClient 已通过工厂分离
- `avp/eval_parallel.py` — 子进程调度，config 透传
- `avp/video_utils.py` — clip 创建逻辑不变（可能需要微调音频保留）

## Key Risks

| 风险 | 影响 | 缓解 |
|------|------|------|
| DashScope 不接受 file:// 本地路径 | 无法用 openai SDK 直传本地文件 | 回退: 用 base64 data URL 或 dashscope SDK |
| QWEN_API_KEY 不是 DashScope key | 无法访问 API | 需要用户提供 DashScope API key |
| 视频太大 (>200MB) | API 拒绝 | prefer_compressed=true + 重编码逻辑已有 |
| 流式响应格式差异 | chunk 解析失败 | openai SDK 兼容，格式标准 |
| Qwen3-Omni 不可用 | 模型 404 | 降级到 qwen3-omni-flash |
| 网络延迟 (DashScope 中国) | 超时 | 增加 timeout，用 stream 减少感知延迟 |

## Errors Encountered
| Error | Attempt | Resolution |
|-------|---------|------------|
| (暂无) | — | — |
