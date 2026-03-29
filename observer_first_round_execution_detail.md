# Observer 首轮全局扫描执行细节分析

> 当 Planner 指定 `load_mode="uniform"`, `fps=1.0` 对 1660 秒篮球比赛视频进行首轮全局扫描时，系统内部到底发生了什么？

---

## 1. 执行总览

```
┌─────────────────────────────────────────────────────────────────────────┐
│                     首轮全局扫描执行时间线                                │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  Planner 输出 PlanSpec:                                                 │
│    load_mode = "uniform"                                                │
│    fps = 1.0                                                            │
│    spatial_token_rate = "low"                                           │
│    regions = []                                                         │
│         │                                                               │
│         ▼                                                               │
│  Observer.observe() 入口                                                │
│    ├─ 计算时间范围: start=0.0s, end=1660.01s (全视频)                    │
│    ├─ 判断: uniform + 1660s ≥ 900s → 触发 ffmpeg 重编码                  │
│    │                                                                    │
│    ├─ [ffmpeg] 重编码全视频                                              │
│    │   480p, 220kbps, 6fps, 无音频                                      │
│    │   输出: step_1_uniform_reencoded_0_1660010.mp4 (~45MB)              │
│    │                                                                    │
│    ├─ 读取 clip 二进制 → Blob → Part                                    │
│    │   fps 调整: 1.0 → 0.31 (受 max_frame=512 限制)                     │
│    │   VideoMetadata(fps=0.31)                                          │
│    │                                                                    │
│    ├─ 组装 contents = [推理prompt文本, Part(视频blob)]                    │
│    │                                                                    │
│    ├─ 调用 Gemini API                                                   │
│    │   model: gemini-2.5-pro                                            │
│    │   config: media_resolution=MEDIA_RESOLUTION_LOW                    │
│    │                                                                    │
│    └─ 解析响应 → Evidence 对象                                           │
│       key_evidence: [{timestamp_start, timestamp_end, description}...]   │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 2. 逐步代码追踪

### 2.1 Observer.observe() 入口

**文件**: `avp/main.py:1794`

```python
def observe(self, plan: PlanSpec, bb: Blackboard,
            store: "Store" = None, round_id: int = 0) -> Evidence:
```

Observer 从 `plan.watch` 读取 WatchConfig：

```python
watch_cfg = plan.watch
# watch_cfg.load_mode           → "uniform"
# watch_cfg.fps                 → 1.0
# watch_cfg.spatial_token_rate  → SpatialTokenRate.low
# watch_cfg.regions             → []
```

### 2.2 时间范围计算

**文件**: `avp/main.py:1762-1778`

```python
def _compute_time_range(self, watch, duration, bb=None):
    if watch.load_mode == "uniform":
        return (0.0, duration)   # ← 全视频: (0.0, 1660.01)
```

| 参数 | 值 |
|------|-----|
| `start_sec` | `0.0` |
| `end_sec` | `1660.01` |
| 含义 | 扫描整个视频，不做时间裁剪 |

### 2.3 进入 infer_on_video() — 决定 clip 策略

**文件**: `avp/main.py:1243-1247`

```python
use_reencoded_uniform_clip = (
    watch_cfg.load_mode == "uniform"              # ✅ True
    and isinstance(duration_sec, (int, float))     # ✅ True
    and float(duration_sec) >= 900.0               # ✅ True (1660.01 >= 900)
)
```

**判定结果**: 视频 1660 秒 > 900 秒阈值 → **走 ffmpeg 重编码路径**

> 如果视频 < 900 秒（15分钟），则直接发送原始视频文件，不经过 ffmpeg。

### 2.4 重编码参数选择

**文件**: `avp/main.py:1248-1267`

根据 `spatial_token_rate` 选择预设：

```python
if media_res == "low":          # ← 本次走这个分支
    scale_width = 480
    video_bitrate = "220k"
    audio_bitrate = None         # 移除音频
    frame_rate = 6.0
    crf = 32
```

三个预设对比：

| 预设 | 分辨率 | 视频码率 | 音频 | 帧率 | 估算文件大小(1660s) |
|------|--------|---------|------|------|-------------------|
| **low** ← 本次 | 480p | 220 kbps | ❌ 移除 | 6 fps | **~45 MB** |
| medium | 640p | 320 kbps | 32 kbps | 8 fps | ~73 MB |
| high | 854p | 550 kbps | 48 kbps | 10 fps | ~122 MB |

### 2.5 ffmpeg 实际执行的命令

**文件**: `avp/video_utils.py:762-788` (`create_reencoded_video_clip`)

对于本次 Q1 测试，实际执行的 ffmpeg 命令等效于：

```bash
ffmpeg \
  -hide_banner \
  -loglevel error \
  -ss 00:00:00.000 \                    # 起始时间 (0秒)
  -to 00:27:40.010 \                    # 结束时间 (1660.01秒)
  -i Basketball_Full_008_2.mp4 \        # 输入: 原始完整视频
  -vf scale=480:-2 \                    # 缩放到宽度480px，高度自适应(保持比例)
  -c:v libopenh264 \                    # 视频编码器 (备选: mpeg4)
  -b:v 220k \                           # 视频码率 220kbps
  -movflags +faststart \                # MP4 快速启动 (moov atom 前置)
  -pix_fmt yuv420p \                    # 像素格式 (标准兼容性)
  -avoid_negative_ts make_zero \        # 时间戳处理
  -r 6.0 \                              # 输出帧率 6fps
  -an \                                  # 移除音频轨道
  -y \                                   # 覆盖输出文件
  temp_clips/step_1_uniform_reencoded_0_1660010.mp4
```

**编码器回退机制**: 优先尝试 `libopenh264`，失败则尝试 `mpeg4`：

```python
encoder_candidates = ["libopenh264", "mpeg4"]
for video_encoder in encoder_candidates:
    # 构建命令并执行
    result = subprocess.run(cmd, ...)
    if result.returncode == 0:
        break   # 成功，跳出
```

**输出文件命名规则**:
```
step_{step_id}_uniform_reencoded_{start_ms}_{end_ms}.mp4
→ step_1_uniform_reencoded_0_1660010.mp4
```

### 2.6 重编码后的视频特征

| 属性 | 原始视频 | 重编码后 |
|------|---------|---------|
| 分辨率 | 1920×1080 (估) | **480×270** |
| 码率 | ~5-10 Mbps | **220 kbps** (压缩 20-45 倍) |
| 帧率 | 25/30 fps | **6 fps** |
| 音频 | 有 | **无** |
| 时长 | 1660 秒 | **1660 秒** (不变) |
| 文件大小 | ~1-2 GB | **~45 MB** |
| 总帧数 | ~41,500-49,800 | **~9,960** (6×1660) |

### 2.7 FPS 调整 — Gemini 端采样

**文件**: `avp/main.py:807-814`

重编码后的 clip 仍然是 1660 秒、6fps。但 Gemini API 的 `VideoMetadata.fps` 控制模型实际看多少帧。系统有最大帧数限制：

```python
max_frame = self.max_frame_low   # 512 帧

expected_frames = fps * duration  # 1.0 × 1660 = 1660 帧
if expected_frames > max_frame:   # 1660 > 512 → 需调整
    adjusted_fps = max_frame / duration
    # adjusted_fps = 512 / 1660 = 0.3084
```

**计算过程**:

| 步骤 | 计算 | 结果 |
|------|------|------|
| Planner 请求的 fps | — | 1.0 |
| 预期帧数 | 1.0 × 1660 | 1660 帧 |
| max_frame_low 限制 | — | 512 帧 |
| 超限? | 1660 > 512 | ✅ 是 |
| 调整后 fps | 512 ÷ 1660 | **0.31 fps** |
| 实际采样帧数 | — | **~512 帧** |
| 平均每帧间隔 | 1660 ÷ 512 | **~3.24 秒/帧** |

> **关键发现**: 不论 Planner 请求 fps=0.5 还是 fps=1.0，对于 1660 秒视频在 low 分辨率下，最终 Gemini 实际处理的都是 **~512 帧**，约每 3.2 秒一帧。

### 2.8 构建 Part 对象 — 视频数据如何包装

**文件**: `avp/main.py:758-833`

```python
# 1. 读取重编码后的 clip 到内存
with open("step_1_uniform_reencoded_0_1660010.mp4", "rb") as f:
    video_data = f.read()   # ~45 MB 二进制数据

# 2. 创建 Blob
blob = Blob(
    mime_type="video/mp4",
    data=video_data          # ~45 MB 内嵌在请求中
)

# 3. 创建 VideoMetadata
video_metadata = VideoMetadata(
    fps=0.31                 # 调整后的 fps
    # 无 startOffset / endOffset (因为 clip 已经是完整范围)
)

# 4. 组装 Part
part = Part(
    inlineData=blob,
    videoMetadata=video_metadata
)
```

### 2.9 Prompt 构建 — Observer 看到的文字指令

**文件**: `avp/prompt.py:365-510` (`get_inference_prompt`)

对于首轮全局扫描，prompt 大致结构：

```text
You are analyzing a video segment to answer a specific question.

**User Query:** 在比赛进行到第二节还剩八分钟时，猛龙队2号球员的一次快攻得分，
以下哪个事件发生的顺序与视频完全一致？

**Video Information:** The video duration is 1660.01s.
You are analyzing the segment from 0.0s to 1660.01s.

**Context from Previous Rounds:**
None (first step)

---

**Your Task:**
Carefully watch the video segment and provide:

1. **Detailed Observations**: What do you see that's relevant to the query?
2. **Key Timestamp Ranges**: For each important event, provide a time interval
   (start and end timestamps in seconds) where the event occurs
3. **Reasoning**: Explain your observations and findings

**Important Guidelines:**
- Canonical timestamp reference: raw_video_seconds (seconds from ORIGINAL video start)
- All timestamps must be in seconds from the ORIGINAL video start
- Events should be represented as time intervals (timestamp_start, timestamp_end)
- Round intervals to full seconds: floor(timestamp_start), ceil(timestamp_end)
- If there is a fast-break or scoring sequence, describe the full sequence...

Return your response as JSON:
{
  "detailed_response": "...",
  "key_evidence": [
    {"timestamp_start": ..., "timestamp_end": ..., "description": "..."},
    ...
  ],
  "reasoning": "..."
}
```

### 2.10 API 调用 — 最终发送给 Gemini 的数据

**文件**: `avp/main.py:1387-1399`

```python
contents = [prompt_text, video_part]
# contents[0]: 上述 prompt 文本 (~2-4 KB)
# contents[1]: Part(inlineData=Blob(~45MB mp4), videoMetadata(fps=0.31))

resp = self.client.models.generate_content(
    model="gemini-2.5-pro",
    contents=contents,
    config=GenerateContentConfig(
        media_resolution="MEDIA_RESOLUTION_LOW"
    )
)
```

**发给 Gemini 的完整数据包**:

| 组成部分 | 类型 | 大小 | 内容 |
|---------|------|------|------|
| `contents[0]` | `str` | ~3 KB | 推理 prompt（含问题、时间范围、输出格式要求） |
| `contents[1]` | `Part` | ~45 MB | 重编码视频 blob（480p, 220kbps, 6fps, 无声） |
| `config.media_resolution` | `str` | — | `"MEDIA_RESOLUTION_LOW"` |

### 2.11 响应解析 — 从 Gemini 回复到 Evidence

**文件**: `avp/main.py:1400-1518`

```python
# 1. 获取文本响应
response_text = resp.text

# 2. 解析 JSON
evidence_data = parse_json_response(response_text)
# evidence_data = {
#   "detailed_response": "在视频约65-67秒处，76人队21号球员恩比德在低位持球...",
#   "key_evidence": [
#     {"timestamp_start": 65, "timestamp_end": 67, "description": "恩比德低位持球"},
#     {"timestamp_start": 67, "timestamp_end": 69, "description": "伦纳德抢断"},
#     {"timestamp_start": 69, "timestamp_end": 72, "description": "伦纳德快攻扣篮"}
#   ],
#   "reasoning": "通过分析视频，在第二节还剩约八分钟时..."
# }

# 3. 时间戳归一化 (取整到全秒)
# floor(65.3) → 65, ceil(71.7) → 72
rounded = round_intervals_full_seconds(raw_ranges, duration=1660.01)

# 4. 构造 Evidence 对象
return Evidence(
    detailed_response="在视频约65-67秒处...",
    key_evidence=[
        {"timestamp_start": 65, "timestamp_end": 67, "description": "恩比德低位持球"},
        {"timestamp_start": 67, "timestamp_end": 69, "description": "伦纳德抢断"},
        {"timestamp_start": 69, "timestamp_end": 72, "description": "伦纳德快攻扣篮"},
    ],
    reasoning="通过分析视频...",
    frames_used=[{"start": 0.0, "end": 1660.01, "fps": 0.31}],
    model_call={
        "model": "gemini-2.5-pro",
        "fps": 0.31,
        "media_resolution": "low",
        "media_inputs": [{
            "input_type": "reencoded_full_range_clip",
            "clip_path": ".../step_1_uniform_reencoded_0_1660010.mp4",
            "absolute_start_sec": 0.0,
            "absolute_end_sec": 1660.01,
        }]
    },
    round_id=1
)
```

---

## 3. Q1 测试的实际运行数据

从 Q1 低 FPS 守卫测试的运行日志中提取：

### 3.1 Planner 输出的 plan.initial.json

```json
{
  "plan_version": "v1",
  "query": "在比赛进行到第二节还剩八分钟时，猛龙队2号球员的一次快攻得分，
            以下哪个事件发生的顺序与视频完全一致？",
  "watch": {
    "load_mode": "uniform",
    "fps": 1.0,
    "spatial_token_rate": "medium",
    "regions": []
  },
  "description": "Scan the entire video to locate the fast break score...",
  "completion_criteria": "Observation is complete when the specified 
                          basketball play has been located...",
  "final_answer": null,
  "complete": false
}
```

### 3.2 实际生成的 clip 文件

从运行日志：
```
🗑️  Deleted clip: step_1_uniform_reencoded_0_1660010.mp4   ← Round 1: 全局重编码
🗑️  Deleted clip: step_1_region_0_64000_72000.mp4           ← Round 2: 区域裁切
🧹 Cleaned up 2 clip(s)
```

| 轮次 | clip 文件名 | ffmpeg 操作 | 时间范围 | 用途 |
|------|------------|------------|---------|------|
| Round 1 | `step_1_uniform_reencoded_0_1660010.mp4` | **重编码** (480p/220k) | 0s → 1660s | 全局粗扫描 |
| Round 2 | `step_1_region_0_64000_72000.mp4` | **流复制** (无损) | 64s → 72s | 证据验证 |

### 3.3 Round 1 Reflector 判定

```json
{
  "sufficient": false,
  "query_confidence": 0.45,
  "reasoning": "Evidence analysis: 1 round(s), 1 unique region(s), 
    3 total evidence items. Query confidence: 0.45 
    [低FPS守卫] 本轮使用 fps=1.0 粗扫描，证据需更高分辨率二次验证，强制 insufficient。"
}
```

低 FPS 守卫将原始 confidence=0.8 强制降至 0.45，触发 replan。

### 3.4 Round 2 Replan → 高 FPS 区域观测

Replan 后 Planner 输出新计划，对 64-72 秒区域以更高 fps 重新观测：
- clip: `step_1_region_0_64000_72000.mp4`（8 秒片段，流复制，原始画质）
- fps 更高（不受 max_frame 限制，因为片段很短）

---

## 4. 关键数据流总结

```
原始视频                                    Gemini 2.5 Pro 实际看到的
Basketball_Full_008_2.mp4                  
┌─────────────────────────┐                ┌──────────────────────────┐
│ 1920×1080               │                │ 480×270                  │
│ ~25-30 fps              │  ──ffmpeg──▶  │ 6 fps (文件层面)          │
│ ~5-10 Mbps              │  重编码        │ 220 kbps                 │
│ ~1-2 GB                 │                │ ~45 MB                   │
│ 1660 秒                 │                │ 1660 秒                  │
│ ~41,500-49,800 帧       │                │ ~9,960 帧 (文件内)       │
│ 有音频                  │                │ 无音频                   │
└─────────────────────────┘                └──────────────────────────┘
                                                     │
                                                     │ VideoMetadata
                                                     │ fps=0.31
                                                     ▼
                                           ┌──────────────────────────┐
                                           │ Gemini 模型实际采样       │
                                           │                          │
                                           │ 512 帧 (max_frame_low)   │
                                           │ 每 ~3.24 秒取 1 帧       │
                                           │ = 每帧覆盖 ~3.24 秒      │
                                           │                          │
                                           │ 总信息量:                │
                                           │   512 帧 × 480×270       │
                                           │   ≈ 66.4M 像素           │
                                           └──────────────────────────┘
```

**这就是为什么 fps=0.5 的粗扫描容易产生幻觉**：模型每 3.2 秒才看一帧 480p 画面，快攻从抢断到上篮只需 2-3 秒，很可能被完全跳过或只看到模糊的中间帧。
