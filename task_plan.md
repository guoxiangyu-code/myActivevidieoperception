# Task Plan: 方案B — QwenClient 适配层实现（API 模式）

## Goal
在保持 AthenaQA 上层逻辑（Controller/Planner/Observer/Reflector）不变的前提下，编写一个 `QwenClient` 适配层替代 `GeminiClient`，通过 OpenAI 兼容 API（yunwu.ai 代理）使用 **qwen2.5-vl-72b-instruct** 模型进行视频理解任务。

## 技术栈决策
- **SDK**: `openai` Python 包（OpenAI 兼容格式）
- **API 端点**: `https://yunwu.ai`（与 Gemini 共用的 OpenAI 兼容代理）
- **API Key**: `QWEN_API_KEY` 来自 `api_key_config.txt`
- **模型名**: `qwen2.5-vl-72b-instruct`
- **视频输入策略**: 双模式（video_url 原生视频 + frame 帧提取回退）

## Current Phase
Phase 1

## Phases

### Phase 1: 需求分析与接口规格确认 ✅
- [x] 完整梳理 GeminiClient 的公共 API（6 个方法 + 7 个属性）
- [x] 确认 Controller/Planner/Observer/Reflector 对 client 的调用接口
- [x] 确认 Qwen2.5-VL 的视频输入格式（支持 video_url + 帧提取）
- [x] 确认 SDK 选择：OpenAI Python SDK → yunwu.ai 代理
- [x] 确认 API 凭证：`QWEN_API_KEY` + `base_url=https://yunwu.ai`
- [x] 记录所有发现到 findings.md
- **Status:** complete

### Phase 2: AVPConfig 扩展
修改 `avp/config.py`（~25 行新增）：
- [ ] 添加 `backend: str = "gemini"`（取值 `"gemini"` | `"qwen"`）
- [ ] 添加 `qwen_api_key: str = ""`
- [ ] 添加 `qwen_base_url: str = ""`（默认空，运行时读 env `QWEN_BASE_URL` 或 fallback 到 `GEMINI_BASE_URL`）
- [ ] 添加 `qwen_model: str = "qwen2.5-vl-72b-instruct"`
- [ ] 添加 `qwen_plan_model: str = ""`（可选，空则 fallback 到 `qwen_model`）
- [ ] 添加 `qwen_video_mode: str = "video"`（取值 `"video"` | `"frames"` | `"auto"`）
- [ ] 在 `load_config()` 中支持 env 变量 `QWEN_API_KEY`、`QWEN_BASE_URL`、`AVP_BACKEND`
- [ ] 保持向后兼容（默认 `backend="gemini"`，所有 Gemini 行为不变）
- **Status:** pending

### Phase 3: 视频→消息转换模块
在 `avp/video_utils.py` 新增 ~80 行：
- [ ] `extract_frames_from_video(video_path, fps, start_sec, end_sec, max_frames, resize_short_edge) → List[base64_str]`
  - OpenCV 按 fps/时间范围抽帧
  - JPEG 编码 + base64 转换
  - 短边 resize（低→480, 中→640, 高→854）
  - 帧数钳制（max_frame_low/medium/high）
- [ ] `encode_video_to_base64(video_path) → str`
  - 读取整个视频文件 → base64 编码
  - 用于 video_url 模式（`data:video/mp4;base64,...`）
- [ ] `build_qwen_video_content(video_path, mode, fps, start, end, max_frames, resize) → List[dict]`
  - 根据 mode="video"|"frames" 构建 OpenAI 消息内容块
  - video 模式：`[{"type":"video_url","video_url":{"url":"data:video/mp4;base64,..."}}]`
  - frames 模式：`[{"type":"image_url","image_url":{"url":"data:image/jpeg;base64,..."}}, ...]`
- **Status:** pending

### Phase 4: QwenClient 核心类实现
创建 `avp/qwen_client.py`（~650 行）：

#### 4a. 基础结构
- [ ] **`_OpenAICompatProxy` 内部类** — 模拟 `genai.Client` 接口:
  ```python
  class _OpenAICompatProxy:
      """代理对象，使 Reflector 的 self.client.client.models.generate_content() 调用可以工作"""
      def __init__(self, openai_client, default_model):
          self.models = self  # self.models.generate_content() → self.generate_content()
          self._client = openai_client
          self._model = default_model
      def generate_content(self, model, contents, config=None):
          # 将 Gemini API 调用转换为 OpenAI 格式
          messages = [{"role": "user", "content": contents}]
          resp = self._client.chat.completions.create(model=model, messages=messages)
          return _CompatResponse(resp.choices[0].message.content)
  ```
  - ⚠️ **关键**: Reflector 在 line 1983 直接调用 `self.client.client.models.generate_content()`

- [ ] **`__init__()`** — 与 GeminiClient 签名兼容:
  - 接受相同参数: model, plan_replan_model, execute_model, project, location, api_key, base_url, debug, max_frame_*, prefer_compressed, keep_temp_clips
  - 额外参数: `qwen_video_mode="video"` (video/frames/auto)
  - 暴露**完全相同的属性**: `prefer_compressed`, `debug`, `model`, `plan_replan_model`, `execute_model`, `keep_temp_clips`, `created_clips`, `temp_clips_dir`, `client` (= _OpenAICompatProxy)

- [ ] **`initialize_client()`** — 创建 OpenAI client + _OpenAICompatProxy:
  ```python
  self._openai_client = openai.OpenAI(api_key=..., base_url=...)
  self.client = _OpenAICompatProxy(self._openai_client, self.plan_replan_model)
  ```

#### 4b. plan() — 纯文本调用 → PlanSpec
- [ ] 完全复制 GeminiClient.plan() 的逻辑结构（行 844-1003）
- [ ] API 调用: `self._openai_client.chat.completions.create(model, messages)`
- [ ] 响应解析: `parse_json_response()` → `validate_against_schema(PLAN_SCHEMA)`
- [ ] Fallback: `self._get_fallback_plan(query)`
- [ ] **关键**: 调用 `_apply_temporal_plan_guards(plan, query, video_meta, debug=self.debug)`（行 979）
- [ ] **关键**: `store.append_role_trace("planner"|"planner_replan", round_id, prompt, response_text)`

#### 4c. create_video_part() — 返回视频内容块
- [ ] 返回 `List[dict]`（OpenAI content 块列表），而非 Gemini `Part`
- [ ] video 模式: `[{"type":"video_url","video_url":{"url":"data:video/mp4;base64,..."}}]`
- [ ] frames 模式: `[{"type":"image_url","image_url":{"url":"data:image/jpeg;base64,..."}}, ...]`
- [ ] FPS 钳制: 完全复制 GeminiClient 行 766-814 的逻辑
- [ ] Duration 计算: 复制 3 策略 fallback（explicit → offsets → metadata）
- [ ] 视频路径解析: 调用 `resolve_video_path(prefer_compressed=...)`

#### 4d. infer_on_video() — 核心视频推理 → Evidence
- [ ] **三条路径结构完全复用 GeminiClient（行 1066-1340）**:
  - PATH A (Region + 多区域): ffmpeg 裁剪 N clips → 每 clip → create_video_part → 组合
  - PATH B (Region + 单区域): ffmpeg 裁剪 1 clip → create_video_part
  - PATH C (Uniform): 整视频 / ≥900s 重编码
  - 每条路径构建完全相同的 `media_inputs` 列表（含 clip_time_base, absolute_start/end 等）
- [ ] **消息组装**（与 Gemini 不同）:
  ```python
  messages = [{"role": "user", "content": [
      *video_content_blocks,  # 来自 create_video_part()
      {"type": "text", "text": prompt}
  ]}]
  ```
- [ ] **GenerateContentConfig 替代**: 不传 media_resolution 到 API，改为在 create_video_part 中控制帧 resize
- [ ] **响应解析**: 完全复用 3 级 fallback（JSON → regex extraction → timestamp ±1s）
- [ ] **关键**: 调用 `_normalize_key_evidence_to_canonical_timebase()`（行 1444）
- [ ] **关键**: 调用 `round_intervals_full_seconds()`（行 1464）
- [ ] **关键**: `store.append_role_trace("observer", round_id, prompt, response_text)`
- [ ] **关键**: 构建 model_call_metadata（行 1486-1509），含 media_inputs 和 time_normalization
- [ ] **关键**: `self.created_clips.append(clip_path)` 跟踪创建的 clip

#### 4e. synthesize_final_answer() — 纯文本调用 → Dict
- [ ] 完全复制 GeminiClient.synthesize_final_answer() 的逻辑（行 1654-1711）
- [ ] 调用 `normalize_final_answer_output()` 归一化输出
- [ ] **关键**: `store.append_role_trace("synthesizer", round_id, prompt, response_text)`

#### 4f. 辅助方法（从 GeminiClient 复制）
- [ ] `_get_fallback_plan()` — fallback 计划（行 1005-1013）
- [ ] `_map_rate_to_media_res()` — spatial_token_rate → "low"|"medium"|"high"（行 836-841）
- [ ] `_extract_timestamps()` — 正则提取时间戳
- [ ] `_extract_confidence()` — 正则提取置信度
- [ ] `_extract_json_field()` — 正则提取 JSON 字段
- [ ] `_extract_key_evidence()` — 正则提取 key_evidence 列表
- **Status:** pending

### Phase 5: 工厂模式与 eval 管线集成
- [ ] 在 `avp/qwen_client.py` 底部添加 `create_client(config) → GeminiClient|QwenClient`
- [ ] 修改 `avp/eval_dataset.py`（~20 行）：
  - 导入 `create_client`
  - 根据 `cfg.backend` 调用工厂函数替代直接 `GeminiClient(...)`
  - Qwen 路径：传入 `qwen_api_key`, `qwen_base_url`, `qwen_model`, `qwen_video_mode`
- [ ] 修改 `avp/main.py`（~5 行）：
  - CLI `run` 命令添加 `--backend` 选项
  - 导入并使用 `create_client`
- [ ] `avp/eval_parallel.py` 无需修改（子进程调度，config 透传）
- [ ] 添加 `openai>=1.0.0` 到 `requirements.txt`
- **Status:** pending

### Phase 6: 测试与验证
- [ ] 单元验证：`extract_frames_from_video()` 帧数/时间范围/resize 正确性
- [ ] 集成验证：`python -m avp.eval_dataset --ann avp/eval_anno/eval_lvbench.json --out avp/out/qwen_test --config avp/config.qwen.example.json --limit 1 --max-turns 1 --timeout 600`
- [ ] 检查输出结构：`results.jsonl`, `summary.json`, `all_sample/sample_0/` 目录完整
- [ ] 记录所有测试结果到 progress.md
- **Status:** pending

### Phase 7: 文档与交付
- [ ] 创建 `avp/config.qwen.example.json` 示例配置
- [ ] 更新 README.md 添加 Qwen 后端使用说明
- [ ] 更新 copilot_change_log.jsonl
- **Status:** pending

## Key Questions（已全部解决）
| # | 问题 | 答案 |
|---|------|------|
| 1 | API 还是本地推理？ | **API**（通过 yunwu.ai OpenAI 兼容代理）|
| 2 | 具体模型版本？ | **qwen2.5-vl-72b-instruct** |
| 3 | API 端点？ | **https://yunwu.ai**（与 Gemini 共用） |
| 4 | API Key 来源？ | **QWEN_API_KEY** from `api_key_config.txt` |
| 5 | SDK 选择？ | **openai** Python 包 |
| 6 | 视频怎么传？ | **双模式**：video_url（base64 整视频）优先，frames（base64 帧图片）回退 |
| 7 | 帧提取工具？ | **OpenCV**（requirements.txt 已包含 opencv-python） |
| 8 | WatchConfig regions？ | **ffmpeg 裁剪 clip → 再传给模型**，裁剪逻辑完全复用 |
| 9 | 接口统一方式？ | **鸭子类型**（Controller 通过 `self.client` 调用，无需 ABC） |

## Decisions Made
| Decision | Rationale |
|----------|-----------|
| 仅支持 API 模式（不支持本地推理） | 用户明确选择 API，72B 模型本地推理不实际 |
| 使用 yunwu.ai 代理 | 用户确认与 Gemini 共用 base_url |
| openai SDK | yunwu.ai 是 OpenAI 兼容代理，openai 包最标准 |
| 双模式视频输入 | video_url 保留 MRoPE 时序编码（最优），frames 作为通用回退 |
| 创建独立 `avp/qwen_client.py` | 不污染 GeminiClient，单一职责 |
| 工厂函数放在 qwen_client.py | 避免创建额外小文件，import 路径简单 |
| 复用 ffmpeg 裁剪流程 | GeminiClient 的三条路径结构可直接移植 |
| 保持 PromptManager 不变 | 所有 prompt 是纯文本，100% 模型无关 |

## Errors Encountered
| Error | Attempt | Resolution |
|-------|---------|------------|
| （暂无） | — | — |

## Notes
- **视频传输大小**: base64 编码膨胀 ~33%；一个 50MB 视频 → ~67MB base64 字符串。需注意 API 请求体大小限制。
- **MRoPE 优势**: Qwen2.5-VL 使用 MRoPE 对每帧编码绝对时间位置，即使以帧图片输入也能保持一定时序理解（优于其他 VLM）。
- **FPS 钳制逻辑**: 从 Gemini 服务端 → Qwen 客户端；公式不变 `fps = min(fps, max_frame / duration)`。
- **不需修改的文件**: `prompt.py`, `eval_parallel.py`, 所有数据类（WatchConfig, PlanSpec, Evidence, Blackboard, Store）。
- 如果 video_url 模式被代理拒绝（400/413），自动降级为 frames 模式。
