# Gemini 2.5 Pro Data Flow Analysis

> What data is passed to the Gemini model, and how does ffmpeg participate in the pipeline?

---

## 1. Architecture Overview

The system has **three LLM-calling roles**, each sending different data to Gemini 2.5 Pro:

```
┌──────────────────────────────────────────────────────────────────────┐
│                        Controller.run() Loop                         │
│                                                                      │
│  ┌─────────┐     ┌──────────┐     ┌───────────┐     ┌────────────┐  │
│  │ Planner  │────▶│ Observer │────▶│ Reflector │────▶│Synthesizer │  │
│  │ (text)   │     │(text+vid)│     │(heuristic)│     │  (text)    │  │
│  └─────────┘     └──────────┘     └───────────┘     └────────────┘  │
│   model call #1   model call #2    NO model call     model call #3   │
│                                                                      │
│  Input:           Input:           Input:            Input:          │
│  - Query text     - Query text     - Evidence list   - Query text   │
│  - Duration       - Video blob(s)  - Plan metadata   - All evidence │
│  - Prior evidence - media_res cfg  (pure Python)     - Options      │
└──────────────────────────────────────────────────────────────────────┘
```

| Role | Model Config Field | What Gets Sent | Receives Video? |
|------|-------------------|----------------|:---------------:|
| **Planner** | `plan_replan_model` | Text prompt only (query + duration + prior evidence) | ❌ |
| **Observer** | `execute_model` | Text prompt + **video binary blob(s)** + `GenerateContentConfig(media_resolution)` | ✅ |
| **Reflector** | — | No model call (heuristic logic) | ❌ |
| **Synthesizer** | `plan_replan_model` | Text prompt only (query + all evidence summaries) | ❌ |

**Key insight**: Only the **Observer** sends video data to Gemini. The Planner and Synthesizer receive text-only prompts.

---

## 2. How Video Data Reaches Gemini (The Observer Path)

### 2.1 API Call Site

```python
# main.py, lines 1387-1399
contents = [prompt] + parts   # [text_string, Part(video_blob_1), Part(video_blob_2), ...]

resp = self.client.models.generate_content(
    model=self.execute_model,          # "gemini-2.5-pro"
    contents=contents,
    config=GenerateContentConfig(
        media_resolution="MEDIA_RESOLUTION_LOW"  # or MEDIUM, HIGH
    )
)
```

### 2.2 Video Binary Embedding (NOT File Upload)

The video is **read as raw bytes and embedded inline** in the API request — no separate file upload:

```python
# main.py, lines 758-760, 833
with open(video_path, "rb") as f:
    video_data = f.read()

blob = Blob(mime_type="video/mp4", data=video_data)
part = Part(inlineData=blob, videoMetadata=VideoMetadata(fps=fps, ...))
```

### 2.3 The `contents` List Structure

```
contents = [
    "You are analyzing a video segment...",   # Text prompt (str)
    Part(                                      # Video blob 1
        inlineData=Blob(
            mime_type="video/mp4",
            data=<raw bytes of clip or full video>
        ),
        videoMetadata=VideoMetadata(
            fps=<adjusted_fps>,
            start_offset="10s",   # optional
            end_offset="20s"      # optional
        )
    ),
    Part(...)                                  # Video blob 2 (if multi-region)
]
```

---

## 3. FFmpeg's Role: Video Clip Preparation

**FFmpeg is used BEFORE the API call** to extract and/or re-encode video segments. There are **two distinct ffmpeg operations**:

### 3.1 Stream-Copy Clip Extraction (Region Mode)

Used for **region-mode observations** — fast, lossless temporal trimming:

```bash
ffmpeg -hide_banner -loglevel error \
  -ss HH:MM:SS.mmm \          # Start time
  -to HH:MM:SS.mmm \          # End time
  -i input.mp4 \
  -c copy \                    # No re-encoding — direct stream copy
  -avoid_negative_ts make_zero \
  -y output_clip.mp4
```

- **When**: `load_mode="region"` (Planner requests a specific time window)
- **Function**: `video_utils.py:create_video_clip()` (line 580)
- **Speed**: Very fast (no decode/encode)
- **Quality**: Identical to source
- **Output**: `temp_clips/step_{id}_region_{idx}_{start_ms}_{end_ms}.mp4`

### 3.2 Re-Encoded Clip (Uniform Mode, Long Videos)

Used when scanning the **entire video** and duration ≥ 900s (15 minutes):

```bash
ffmpeg -hide_banner -loglevel error \
  -ss HH:MM:SS.mmm -to HH:MM:SS.mmm \
  -i input.mp4 \
  -vf scale={width}:-2 \           # Downscale resolution
  -c:v libopenh264 \               # Re-encode video (fallback: mpeg4)
  -b:v {bitrate} \                 # Target bitrate
  -r {frame_rate} \                # Output frame rate
  -movflags +faststart \           # MP4 streaming optimization
  -pix_fmt yuv420p \               # Standard pixel format
  -avoid_negative_ts make_zero \
  [-c:a aac -b:a {audio_br} | -an] \  # Audio: AAC or removed
  -y output_reencoded.mp4
```

- **When**: `load_mode="uniform"` AND `duration ≥ 900s`
- **Function**: `video_utils.py:create_reencoded_video_clip()` (line 700)
- **Purpose**: Compress long videos to fit within API upload limits

**Three quality presets** controlled by `spatial_token_rate`:

| Preset | Width | Video Bitrate | Audio | FPS | Typical Use |
|--------|-------|---------------|-------|-----|-------------|
| **low** | 480px | 220 kbps | removed | 6.0 | Fast coarse scan |
| **medium** | 640px | 320 kbps | 32 kbps | 8.0 | Balanced |
| **high** | 854px | 550 kbps | 48 kbps | 10.0 | Detail verification |

### 3.3 No Clip at All (Short Uniform Videos)

When `load_mode="uniform"` AND video < 900s, the **original full video** is sent directly:

```python
part = create_video_part(
    video_path=original_video,     # No ffmpeg, no clip
    fps=fps,
    start_offset=f"{start_sec}s",  # Metadata tells Gemini what to look at
    end_offset=f"{end_sec}s",
    media_resolution=media_res
)
```

---

## 4. Complete Data Flow Diagram

```
User Query + Video Path
        │
        ▼
┌─── PLANNER ──────────────────────────────────────────────────────┐
│  Input to Gemini:                                                │
│    contents = [ planning_prompt_text ]                            │
│                                                                  │
│  Planning prompt includes:                                       │
│    • User query                                                  │
│    • Video duration (seconds)                                    │
│    • Prior evidence (if replan)                                  │
│    • Planning framework (uniform vs region, fps, resolution)     │
│                                                                  │
│  Output: PlanSpec with WatchConfig                               │
│    { load_mode, fps, spatial_token_rate, regions }               │
└──────────────────────────────────────────────────────────────────┘
        │
        │  WatchConfig determines clip strategy:
        │
        ├─ load_mode="uniform" + duration < 900s
        │    → Full video sent (no ffmpeg)
        │
        ├─ load_mode="uniform" + duration ≥ 900s
        │    → ffmpeg re-encode (downscale + compress)
        │
        └─ load_mode="region"
             → ffmpeg stream-copy (trim only, no quality loss)
        │
        ▼
┌─── OBSERVER ─────────────────────────────────────────────────────┐
│  Input to Gemini:                                                │
│    contents = [                                                  │
│        inference_prompt_text,      # Query + time mapping + ctx  │
│        Part(video_blob_1),         # Binary video data           │
│        Part(video_blob_2),         # (if multi-region)           │
│        ...                                                       │
│    ]                                                             │
│    config = GenerateContentConfig(                               │
│        media_resolution = "MEDIA_RESOLUTION_LOW|MEDIUM|HIGH"     │
│    )                                                             │
│                                                                  │
│  Inference prompt includes:                                      │
│    • Sub-query derived from plan                                 │
│    • Video duration and region info                              │
│    • Canonical time conversion rules (clip-local → original)     │
│    • Context from previous observation rounds                    │
│                                                                  │
│  Output: Evidence                                                │
│    { detailed_response, key_evidence: [{ts_start, ts_end, ...}]} │
└──────────────────────────────────────────────────────────────────┘
        │
        ▼
┌─── REFLECTOR (No Model Call) ────────────────────────────────────┐
│  Pure Python heuristic:                                          │
│    • Count evidence items (≥ 3 → confidence 0.8)                 │
│    • Check for detailed responses                                │
│    • Low-FPS guard: if fps ≤ 1.0 && first round → force replan  │
│                                                                  │
│  Output: { sufficient: bool, query_confidence: float }           │
└──────────────────────────────────────────────────────────────────┘
        │
        │ if sufficient=true
        ▼
┌─── SYNTHESIZER ──────────────────────────────────────────────────┐
│  Input to Gemini:                                                │
│    contents = [ synthesis_prompt_text ]                           │
│                                                                  │
│  Synthesis prompt includes:                                      │
│    • Original user query                                         │
│    • MCQ options (if applicable)                                 │
│    • Video duration                                              │
│    • ALL evidence from ALL rounds (text summaries)               │
│    • Guidelines for confidence scoring                           │
│                                                                  │
│  Output: Final answer                                            │
│    { answer, confidence, evidence_summary, key_timestamps }      │
└──────────────────────────────────────────────────────────────────┘
```

---

## 5. WatchConfig: The Parameters Controlling What Video Gemini Sees

```python
@dataclass
class WatchConfig:
    load_mode: str                        # "uniform" | "region"
    fps: float                            # Temporal sampling rate
    spatial_token_rate: SpatialTokenRate   # "low" | "medium"
    regions: List[Tuple[float, float]]    # [(start_sec, end_sec), ...]
```

### FPS Auto-Adjustment

The system enforces maximum frame limits per resolution:

| Resolution | Max Frames | Example: 1660s video at fps=1.0 |
|------------|------------|----------------------------------|
| low | 512 | 1660 frames requested → adjusted to 512/1660 = **0.31 fps** |
| medium | 128 | 1660 frames → **0.077 fps** |
| high | 128 | 1660 frames → **0.077 fps** |

### Mapping to Gemini API

| `spatial_token_rate` | → `media_resolution` (API) |
|---------------------|---------------------------|
| `"low"` | `MEDIA_RESOLUTION_LOW` |
| `"medium"` | `MEDIA_RESOLUTION_MEDIUM` |

---

## 6. Summary: When FFmpeg Is and Isn't Used

| Scenario | FFmpeg Used? | Operation | Quality |
|----------|:----------:|-----------|---------|
| Planner call | ❌ | Text only, no video | N/A |
| Synthesizer call | ❌ | Text only, no video | N/A |
| Observer: uniform, short video (<15min) | ❌ | Full video sent directly | Original |
| Observer: uniform, long video (≥15min) | ✅ **Re-encode** | Downscale + compress + limit fps | Reduced |
| Observer: region mode | ✅ **Stream copy** | Trim to time window, no quality loss | Original |
| Observer: clip creation failed | ❌ | Fallback to full video + offset metadata | Original |

**Bottom line**: FFmpeg serves as a **pre-processing step** to prepare video data before it's embedded in the Gemini API request. It either trims (stream copy) or compresses (re-encode) video segments so they're small enough and focused enough for efficient API consumption.
