"""
Prompt Management for Agentic Video Understanding Framework
===========================================================

This module centralizes all prompts and response schemas for the framework.
Uses structured outputs (JSON) for reliable parsing.
"""

from typing import Dict, Any, Optional, List, Tuple
import json


# ======================================================
# JSON Schemas for Structured Outputs
# ======================================================

PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "reasoning": {"type": "string", "description": "Brief explanation of the planning strategy"},
        "steps": {
            "type": "array",
            "minItems": 1,
            "maxItems": 1,
            "description": "Array containing exactly one observation action",
            "items": {
                "type": "object",
                "properties": {
                    "step_id": {"type": "string", "description": "Always '1' for single-action mode"},
                    "description": {"type": "string", "description": "Goal/reasoning objective for this observation"},
                    "sub_query": {"type": "string", "description": "Query for this observation (should match original query)"},
                    "load_mode": {"type": "string", "enum": ["uniform", "region"], "description": "uniform=full video, region=specific time spans"},
                    "fps": {"type": "number", "minimum": 0.1, "maximum": 5.0, "description": "Temporal sampling rate"},
                    "spatial_token_rate": {"type": "string", "enum": ["low", "medium"], "description": "Spatial resolution"},
                    "regions": {
                        "type": "array",
                        "items": {
                            "type": "array",
                            "items": {"type": "number"},
                            "minItems": 2,
                            "maxItems": 2
                        },
                        "default": [],
                        "description": "Time spans [[start, end]] in seconds (empty for uniform mode)"
                    }
                },
                "required": ["step_id", "description", "sub_query", "load_mode", "fps", "spatial_token_rate"]
            }
        },
        "completion_criteria": {"type": "string"}
    },
    "required": ["reasoning", "steps", "completion_criteria"]
}


EVIDENCE_SCHEMA = {
    "type": "object",
    "properties": {
        "detailed_response": {"type": "string", "description": "Detailed analysis and observations relevant to the sub-query"},
        "key_evidence": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "timestamp_start": {"type": "number", "description": "Start timestamp of the event in seconds"},
                    "timestamp_end": {"type": "number", "description": "End timestamp of the event in seconds"},
                    "description": {"type": "string", "description": "What happens during this time interval"}
                },
                "required": ["timestamp_start", "timestamp_end", "description"]
            },
            "description": "List of key evidence with timestamp ranges and descriptions"
        },
        "reasoning": {"type": "string", "description": "Explanation of findings and observations"}
    },
    "required": ["detailed_response", "key_evidence", "reasoning"]
}


REFLECTION_SCHEMA = {
    "type": "object",
    "properties": {
        "sufficient": {"type": "boolean", "description": "Whether the current evidence is sufficient to answer the query with confidence"},
        "should_update": {"type": "boolean", "description": "Whether the controller should continue watching and replan"},
        "answerability_confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0, "description": "Confidence that the current evidence supports a reliable answer"},
        "missing_information": {"type": "string", "description": "What is still missing from the evidence"},
        "next_search_strategy": {"type": "string", "description": "High-level guidance for the next observation, e.g. broaden, shift, uniform, refine, stop"},
        "reasoning": {"type": "string", "description": "Explanation of the reflection decision"}
    },
    "required": [
        "sufficient",
        "should_update",
        "answerability_confidence",
        "missing_information",
        "next_search_strategy",
        "reasoning",
    ]
}


# PLAN_UPDATE_SCHEMA removed in simplified loop


FINAL_ANSWER_SCHEMA = {
    "type": "object",
    "properties": {
        "answer": {"type": "string", "description": "Direct answer to the user's query"},
        "key_timestamps": {
            "type": "array",
            "items": {"type": "number"},
            "description": "Most relevant timestamps"
        },
        "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "evidence_summary": {"type": "string", "description": "Brief summary of supporting evidence"}
    },
    "required": ["answer", "key_timestamps", "confidence", "evidence_summary"]
}

MCQ_SCHEMA = {
    "type": "object",
    "properties": {
        "selected_option": {
            "type": "string",
            "description": "The chosen option letter",
            "enum": ["A", "B", "C", "D", "E", "F"]
        },
        "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "reasoning": {"type": "string"},
        "selected_option_text": {"type": "string"}
    },
    "required": ["selected_option", "confidence", "reasoning"]
}

# ======================================================
# Prompt Templates
# ======================================================

class PromptManager:
    """Manages all prompts for the agentic video framework."""

    @staticmethod
    def _build_temporal_context_section(video_meta: Dict[str, Any]) -> str:
        """Format optional time-base and temporal-hint metadata for prompts."""
        lines: List[str] = []

        time_base = str(video_meta.get("time_base", "") or "").strip()
        if time_base:
            lines.append(f"- Time Base: {time_base}")

        time_reference = str(video_meta.get("time_reference", "") or "").strip()
        if time_reference:
            lines.append(f"- Time Reference: {time_reference}")

        temporal_hint_summary = str(video_meta.get("temporal_hint_summary", "") or "").strip()
        if temporal_hint_summary:
            lines.append(f"- Temporal Hint Summary: {temporal_hint_summary}")

        if not lines:
            return ""

        return "\n\n    **Temporal Context / Time-Base Notes:**\n    " + "\n    ".join(lines)


    @staticmethod
    def get_planning_prompt(query: str, video_meta: Dict[str, Any], options: Optional[List[str]] = None) -> str:
        """Generate an initial multi-step plan for video analysis (smarter version).

        - Enforces single-step plan per round (ONE observation action only).
        - Enforces ≥30s segments for specific events/actions when timing is vague.
        - Handles explicit timestamps in the query:
            * "at 1:23"  -> use a ±15-30s window around that point
            * "from 2:00 to 2:30" / "between 2:00 and 2:30" -> use [120, 150]
        - Includes few-shot exemplars (opening caption, after-eating dependency, end-of-video).
        - For single-step plans, uses the original query as sub_query.

        Args:
            query: User's question about the video
            video_meta: Video metadata (duration)
            options: Optional list of options for MCQ questions
        """
        # Accept several common keys; fall back to "unknown"
        duration = (
            video_meta.get("duration_sec")
            or video_meta.get("duration")
            or video_meta.get("video_duration_sec")
            or "unknown"
        )
        temporal_context_section = PromptManager._build_temporal_context_section(video_meta)
        
        # Build the full query with options if available
        full_query = query
        if options:
            options_text = "\n".join([f"- {opt}" for opt in options])
            full_query = f"{query}\n\nOptions:\n{options_text}"

        prompt = f"""You are an expert video analysis planner. Create a concise, single-action observation plan (ONE step) to answer the user's query this round.

    **User Query:** {full_query}

    **Video Information:**
    - Duration: {duration} seconds
{temporal_context_section}

    **Planning Framework:**
    Each action A_t in your plan must specify three key components:
    1. **Goal (Reasoning Objective)**: The step's reasoning objective - what you're trying to accomplish
       - Examples: "localize a key event", "recognize a fine-grained cue", "identify an anomaly", 
         "count objects", "determine spatial relationships", "extract text/numbers", "analyze temporal sequence"
       - This should be clearly stated in the "description" field
    2. **Region**: The temporal span in the video to examine
       - Can be the whole video (uniform mode) or specific temporal spans (region mode)
       - Specified via "load_mode" ("uniform" for full video, "region" for specific spans)
       - For region mode, provide exact [start, end] timestamps in seconds in the "regions" field
    3. **Sampling Granularity**: The fps (frames per second) and resolution settings
       - "fps": Controls temporal sampling rate (0.1-5.0, lower = sparser sampling)
       - "spatial_token_rate": Controls spatial resolution ("low" or "medium", lower = coarser spatial detail)

    **Your Planning Strategy:**
    1. **Coarse-to-Fine Strategy**: Start with broad uniform scans (low fps, low resolution) to locate candidate regions, then zoom in with higher detail.
    2. **Efficiency**: Balance thoroughness with computational cost.
    
    **CRITICAL: SINGLE ACTION REQUIREMENT (THIS ROUND):**
    - You MUST output EXACTLY ONE observation action (ONE item in the `steps` array).
    - Set the `sub_query` to the EXACT SAME text as the original user query (including options if provided).
    - Decide the single action’s region/uniform, fps, and spatial_token_rate to best gather query-relevant evidence now.
    
    **Timestamp Handling (CRITICAL - Read Carefully):**
    
    **First, determine the query type:**
    - **Factual questions**: Questions asking about facts, counts, identities, states, or properties (e.g., "what", "how many", "who", "which", "count", "identify", "what color", "what is the number")
    - **Reasoning/explanation questions**: Questions asking about causes, reasons, explanations, processes, or motivations (e.g., "why", "how", "explain", "reason", "cause", "purpose", "why did", "how did", "what led to")
    
    **RULE 1: Exact timestamp ranges with start AND end:**
    - If the query specifies BOTH a start AND end time (e.g., "07:15 - 07:18", "from 2:00 to 2:30", "between 2:00 and 2:30"):
      * **For FACTUAL questions**: Use the EXACT timestamps - DO NOT add padding.
        - Convert directly: "07:15 - 07:18" → [435.0, 438.0] seconds (EXACTLY, no padding)
        - Example: "How many pieces are out at 07:15 - 07:18?" → regions: [[435.0, 438.0]] - use exactly 3 seconds
      * **For REASONING/EXPLANATION questions**: Add padding (15-30 seconds before/after) to provide context.
        - Convert: "07:15 - 07:18" → [435-15, 438+15] = [420.0, 453.0] seconds (adds context)
        - Example: "Why did the player move at 07:15 - 07:18?" → regions: [[420.0, 453.0]] - needs context to understand the reason
    
    **RULE 2: Single specific timestamp (exact time):**
    - If the query mentions a single timestamp WITHOUT "around/near/about" (e.g., "at 1:23", "at 02:15"):
      * **For FACTUAL questions**: Use a forward 1-second window starting from that timestamp.
        - Convert: "at 1:23" (83 seconds) → [83.0, 84.0] for 1 second starting at that exact moment
        - Example: "What is the score at 02:15?" → [135.0, 136.0] - 1 second window starting at 02:15
      * **For REASONING/EXPLANATION questions**: Add padding (15-30 seconds before/after) to understand context.
        - Convert: "at 1:23" (83 seconds) → [83-15, 83+15] = [68.0, 98.0] (30-second window for context)
        - Example: "Why did the player act at 02:15?" → [120.0, 150.0] - needs context to understand why
    
    **RULE 3: Approximate or vague timing:**
    - If the query uses words like "around/near/about this time" (e.g., "around 1:23", "near 02:15"), use a segment window.
    - Convert: "around 1:23" → [83-15, 83+15] = [68.0, 98.0] (30-second window)
    - If the query mentions vague timing without specific seconds (e.g., "near the beginning", "around the end"), use longer segments (30 seconds or more)
    
    **CRITICAL:** 
    - For FACTUAL questions with exact timestamps: respect them precisely (no padding)
    - For REASONING/EXPLANATION questions with exact timestamps: add 15-30 seconds padding before/after to understand context
    - Only use padding/windows when the query explicitly says "around/near/about" or when timing is vague
    - If the temporal context says reference timings are dataset-local or non-authoritative, do NOT map them directly to raw-video seconds

    All explicit timestamps must be interpreted as seconds from the start of the original video.

    **Segment Length Rule:**
    - When targeting a specific event/action with VAGUE timing, use segments that are **at least 30 seconds** long whenever possible.
    - **EXCEPTIONS for exact timestamps:** 
      * **Factual questions** with exact start AND end timestamps: use them EXACTLY - no padding, no 30-second rule.
      * **Factual questions** with a single exact timestamp: use a forward 1-second window starting from that timestamp (e.g., timestamp 45 → [45.0, 46.0]).
      * **Reasoning/explanation questions** with exact timestamps: still add padding (15-30 seconds) to understand context.

    **Heuristic Hints (if duration is known for this single action):**
    - If the query mentions "opening"/"beginning", consider [0, 30].
    - If the query mentions "end"/"ending", consider [max(0, duration - 30), duration].
    - If timing is completely unknown, begin with a uniform scan at low fps (0.25-1.0) and LOW or MEDIUM spatial token rate.
    - If the query asks how long an opening sequence took to reach a score/state/event, prefer a broader opening window (often >= 240s) over a very short clip.

    **Step Configuration Guidelines (choose ONE for this step):**
    - Uniform scan of the full video when timing is unknown
      - load_mode: "uniform"; fps: 0.25–1.0; spatial_token_rate: "low" or "medium"; regions: []
    - Region analysis when explicit timestamps/ranges are given or strongly implied
      - load_mode: "region"; fps: ~2.0; spatial_token_rate: "low" or "medium"; regions: [[start, end]]

    **Few-Shot Exemplars (JSON):**
    
    - MCQ with exact timestamp range - FACTUAL question:
    {{
    "reasoning": "Query specifies exact time range 07:15-07:18. Use region mode with exact timestamps.",
    "steps": [
        {{
        "step_id": "1",
        "description": "Examine the exact time segment and count game pieces",
        "sub_query": "How many total pieces are out of the game at 07:15 - 07:18?\\n\\nOptions:\\nA. 4\\nB. 5\\nC. 3\\nD. 2\\nE. 1",
        "load_mode": "region",
        "fps": 2.0,
        "spatial_token_rate": "medium",
        "regions": [[435.0, 438.0]]
        }}
    ],
    "completion_criteria": "Observation complete when exact time segment is analyzed"
    }}
    
    - Single exact timestamp - FACTUAL question:
    {{
    "reasoning": "Query asks about state at exact timestamp 02:15. Use 1-second forward window.",
    "steps": [
        {{
        "step_id": "1",
        "description": "Check the state at exactly 02:15",
        "sub_query": "What does the player in the top left corner have at 02:15?\\n\\nOptions:\\nA. Red piece\\nB. Yellow piece\\nC. Nothing",
        "load_mode": "region",
        "fps": 2.0,
        "spatial_token_rate": "medium",
        "regions": [[135.0, 136.0]]
        }}
    ],
    "completion_criteria": "Observation complete when exact moment is analyzed"
    }}
    
    - Vague timing query (needs uniform scan):
    {{
    "reasoning": "No specific timing provided. Start with uniform scan to locate the event.",
    "steps": [
        {{
        "step_id": "1",
        "description": "Scan entire video to find when person finishes eating",
        "sub_query": "When does the person finish eating?",
        "load_mode": "uniform",
        "fps": 0.5,
        "spatial_token_rate": "low",
        "regions": []
        }}
    ],
    "completion_criteria": "Observation complete when eating-finish event is located"
    }}

    - End-of-video query:
    {{
    "reasoning": "Query asks about end of video. Focus on last 30 seconds.",
    "steps": [
        {{
        "step_id": "1",
        "description": "Count people in the final scene",
        "sub_query": "How many people are present near the end?",
        "load_mode": "region",
        "fps": 2.0,
        "spatial_token_rate": "medium",
        "regions": [[180, 210]]
        }}
    ],
    "completion_criteria": "Observation complete when final scene is analyzed"
    }}

    **Output Format (STRICT JSON ONLY):**
    The `steps` array MUST contain exactly ONE item.
    Return a single JSON object that validates against this schema:
    {json.dumps(PLAN_SCHEMA, indent=2)}

    Now generate the plan for the user's query. Respond with JSON only, no additional text."""
        
        return prompt
        



    @staticmethod
    def get_inference_prompt(
        sub_query: str,
        context: str,
        start_sec: float,
        end_sec: float,
        original_query: str,
        video_duration_sec: float = None,
        is_region: bool = False,
        regions: List[Tuple[float, float]] = None,
        media_inputs: Optional[List[Dict[str, Any]]] = None,
        time_base: str = "",
        temporal_hint_summary: str = "",
    ) -> str:
        """Generate prompt for video analysis step.
        
        Args:
            sub_query: Specific question for this step
            context: Evidence gathered from previous steps
            start_sec: Start time of video segment
            end_sec: End time of video segment
            original_query: The user's original question
            video_duration_sec: Total duration of the original video in seconds
            is_region: Whether this is a region/clip (True) or uniform mode (False)
        """
        context_text = context if context.strip() else "None (first step)"
        
        # Detect if this is a single-step query (contains "Options:" or is the same as original_query)
        is_single_step = "Options:" in sub_query or sub_query.strip() == original_query.strip()
        
        if is_single_step:
            query_section = f"""**User Query:** {sub_query}"""
        else:
            query_section = f"""**Original User Query:** {original_query}

**Current Sub-Query:** {sub_query}"""
        
        # Build video info sentence
        video_info = ""
        if video_duration_sec:
            if is_region and regions and len(regions) > 1:
                # Multiple clips: identify each clip with its time range
                video_info = f"**Video Information:** The original video duration is {video_duration_sec:.1f}s. You are analyzing {len(regions)} video segments:\n"
                for i, (reg_start, reg_end) in enumerate(regions, 1):
                    video_info += f"- **Clip {i}**: {reg_start:.1f}s to {reg_end:.1f}s of the original video\n"
                video_info = video_info.rstrip()  # Remove trailing newline
            elif is_region:
                video_info = f"**Video Information:** The original video duration is {video_duration_sec:.1f}s. You are analyzing a specific region from {start_sec:.1f}s to {end_sec:.1f}s of the original video."
            else:
                video_info = f"**Video Information:** The video duration is {video_duration_sec:.1f}s. You are analyzing the segment from {start_sec:.1f}s to {end_sec:.1f}s."
        else:
            if is_region and regions and len(regions) > 1:
                # Multiple clips without duration info
                video_info = f"**Video Segments:** You are analyzing {len(regions)} video segments:\n"
                for i, (reg_start, reg_end) in enumerate(regions, 1):
                    video_info += f"- **Clip {i}**: {reg_start:.1f}s to {reg_end:.1f}s (duration: {reg_end - reg_start:.1f}s)\n"
                video_info = video_info.rstrip()
            else:
                video_info = f"**Video Segment:** {start_sec:.1f}s to {end_sec:.1f}s (duration: {end_sec - start_sec:.1f}s)"
        
        # Build guidelines section
        guidelines = """- Canonical timestamp reference for this task is raw_video_seconds: seconds from the start of the ORIGINAL video
- All timestamps must be in seconds from the start of the ORIGINAL video (not relative to this segment)
- Events should be represented as time intervals (timestamp_start, timestamp_end), not single points
- If you see the target event, note the EXACT time range where it occurs
- If you see potential matches, list ALL relevant timestamp ranges
- Be precise with timing - this is critical for narrowing down the search
- Consider the context from previous rounds to avoid redundancy
- IMPORTANT: Round intervals to full seconds: floor(timestamp_start), ceil(timestamp_end)"""

        temporal_context_block = ""
        if temporal_hint_summary:
            temporal_context_block = f"\n**Temporal Context / Time-Base Notes:**\n- Time Base: {time_base or 'raw_video_seconds'}\n- {temporal_hint_summary}\n"
        elif time_base:
            temporal_context_block = f"\n**Temporal Context / Time-Base Notes:**\n- Time Base: {time_base}\n"

        if time_base == "dataset_reference_only":
            guidelines += "\n- Dataset-provided reference timings may use a different clock than the raw video; report timestamps based on the raw-video timeline visible in this observation, not by forcing alignment to annotation reference text."

        clip_inputs = [
            media_input for media_input in (media_inputs or [])
            if media_input.get("clip_time_base") == "clip_local_seconds"
        ]
        media_time_mapping_block = ""
        if clip_inputs:
            if len(clip_inputs) == 1:
                clip_input = clip_inputs[0]
                clip_start = float(clip_input.get("absolute_start_sec", 0.0))
                clip_end = float(clip_input.get("absolute_end_sec", clip_start))
                media_time_mapping_block = f"""
**Canonical Time Conversion for This Media Input:**
- The provided media file is a trimmed clip covering **{clip_start:.1f}s to {clip_end:.1f}s** of the original video.
- The clip itself starts at local clip time **0.0s**.
- If an event appears at local clip time `t`, you MUST convert it to canonical original-video time as **{clip_start:.1f} + t** seconds.
- Example: if something happens 5.0s after this clip begins, report it as **{clip_start + 5.0:.1f}s** in the original video.
"""
            else:
                mapping_lines = []
                for idx, clip_input in enumerate(clip_inputs, 1):
                    clip_start = float(clip_input.get("absolute_start_sec", 0.0))
                    clip_end = float(clip_input.get("absolute_end_sec", clip_start))
                    mapping_lines.append(
                        f"- Clip {idx}: local clip time `t` maps to original-video time **{clip_start:.1f} + t** seconds "
                        f"(absolute range {clip_start:.1f}s to {clip_end:.1f}s)"
                    )
                media_time_mapping_block = (
                    "\n**Canonical Time Conversion for These Media Inputs:**\n"
                    + "\n".join(mapping_lines)
                    + "\n- For every JSON timestamp, output the canonical original-video time after applying the correct clip-specific offset."
                )
            guidelines += "\n- If the provided media is a trimmed clip, never return clip-local seconds directly. Convert local clip time back to raw_video_seconds using the mapping above before writing JSON."
        
        # Add guideline for multiple clips if applicable
        if is_region and regions and len(regions) > 1:
            guidelines += "\n- **When analyzing multiple clips**: Each clip corresponds to a specific time range as listed above. When reporting timestamps, always use the ORIGINAL video timestamps (not relative to the clip). You can reference which clip you observed the event in (e.g., 'Clip 1', 'Clip 2') in your description, but timestamps must always be in seconds from the start of the original video."
        
        prompt = f"""You are analyzing a video segment to answer a specific question.
{query_section}

{video_info}
{temporal_context_block}
{media_time_mapping_block}

**Context from Previous Rounds:**
{context_text}

---

**Your Task:**
Carefully watch the video segment and provide:

1. **Detailed Observations**: What do you see that's relevant to the query?
2. **Key Timestamp Ranges**: For each important event, provide a time interval (start and end timestamps in seconds from video start) where the event occurs
3. **Reasoning**: Explain your observations and findings

**Important Guidelines:**
{guidelines}

**Critical Fallback Strategy:**
- If you're analyzing a REGION (time segment) and you DON'T FIND relevant information in this segment, you MUST explicitly state:
  - "No relevant information found in this time segment"
  - Note that a UNIFORM (full video) scan may be needed to locate the target
  - Indicate in reasoning that the search should expand to the full video or other regions

**Output Format:**
Respond with valid JSON only:
{json.dumps(EVIDENCE_SCHEMA, indent=2)}

**Example Response:**
```json
{{
  "detailed_response": "A person wearing a distinctive red jacket enters the frame from the left side of the screen. The individual then walks directly toward what appears to be a blue sedan parked in the background. At approximately 52 seconds, the person reaches the driver's side door of the blue car, pauses briefly, and then opens the door. The entire sequence is clearly visible with no obstructions.",
  "key_evidence": [
    {{"timestamp_start": 43.0, "timestamp_end": 47.0, "description": "Person in red jacket enters frame from left side"}},
    {{"timestamp_start": 50.0, "timestamp_end": 54.0, "description": "Person reaches blue car's driver side door"}},
    {{"timestamp_start": 53.0, "timestamp_end": 56.0, "description": "Person opens car door"}}
  ],
  "reasoning": "Clear visibility of red jacket and blue car. Person's motion is unambiguous. Timestamp ranges are precise and well-documented."
}}
```

Analyze the video now and respond with JSON only."""
        
        return prompt
    
    # get_verification_prompt removed - Verifier now analyzes evidence without re-watching video
    
    @staticmethod
    def get_replanning_prompt(
        query: str,
        video_meta: Dict[str, Any],
        evidence_summary: str,
        options: Optional[List[str]] = None
    ) -> str:
        """Generate prompt for replanning after insufficient evidence.
        
        Args:
            query: User's question about the video
            video_meta: Video metadata (duration)
            evidence_summary: Summary of all evidence gathered so far
            options: Optional list of options for MCQ questions
        """
        duration = (
            video_meta.get("duration_sec")
            or video_meta.get("duration")
            or video_meta.get("video_duration_sec")
            or "unknown"
        )
        temporal_context_section = PromptManager._build_temporal_context_section(video_meta)
        
        # Build the full query with options if available
        full_query = query
        if options:
            options_text = "\n".join([f"- {opt}" for opt in options])
            full_query = f"{query}\n\nOptions:\n{options_text}"
        
        prompt = f"""You are replanning a video observation after previous evidence was insufficient.

**User Query:** {full_query}

**Video Information:**
- Duration: {duration} seconds
{temporal_context_section}

**Evidence Gathered from Previous Rounds:**
{evidence_summary}

---

**Your Task:**
Based on the evidence gathered so far and what's still missing, plan a NEW single observation action to gather additional evidence.

**Replanning Strategy:**
1. **Analyze what's missing**: What aspects of the query are not yet answered by the evidence?
2. **Avoid redundancy**: Don't re-observe the same regions with the same parameters
3. **Try different approaches**:
   - If previous uniform scan found nothing → try focused regions based on hints in the query
   - If previous region search failed → try uniform scan with different fps/resolution
   - If evidence is ambiguous → try higher fps or different spatial resolution
   - If specific timestamps mentioned in query → focus on those exact regions
   - If the temporal context says the dataset timing is not authoritative for raw-video seconds, use reference timings only as weak hints and prefer broader scans

**Planning Guidelines:**
- load_mode: "uniform" (full video) or "region" (specific time spans)
- fps: 0.1-5.0 (lower = sparser sampling, higher = denser)
- spatial_token_rate: "low" or "medium" (lower = coarser spatial detail)
- regions: [[start, end]] in seconds (empty for uniform mode)

**IMPORTANT:** Generate exactly ONE observation action (steps array must have exactly 1 item).

**Output Format (STRICT JSON ONLY):**
The steps array MUST contain exactly ONE item.
{json.dumps(PLAN_SCHEMA, indent=2)}

**Example Replan (after failed region search):**
```json
{{
  "reasoning": "Previous region search at 100-130s found nothing. Need to scan the full video with uniform sampling to locate the target event.",
  "steps": [
    {{
      "step_id": "1",
      "description": "Uniform scan of entire video to locate target event that was missed in previous region",
      "sub_query": "{query}",
      "load_mode": "uniform",
      "fps": 0.5,
      "spatial_token_rate": "low",
      "regions": []
    }}
  ],
  "completion_criteria": "Plan complete when new observation provides missing evidence"
}}
```

Now generate the replan for the user's query. Respond with JSON only, no additional text."""

        return prompt

    @staticmethod
    def get_answerability_reflection_prompt(
        query: str,
        video_meta: Dict[str, Any],
        evidence_summary: str,
        watched_intervals_text: str,
        provisional_answer: str,
        provisional_confidence: float,
        round_index: int,
        max_rounds: int,
    ) -> str:
        """Generate a generic reflection prompt for answerability-aware control."""
        duration = (
            video_meta.get("duration_sec")
            or video_meta.get("duration")
            or video_meta.get("video_duration_sec")
            or "unknown"
        )

        prompt = f"""You are the reflector/controller in a general video question-answering agent framework.

**User Query:** {query}

**Video Information:**
- Duration: {duration} seconds
- Current Round: {round_index} / {max_rounds}

**Watched Intervals So Far:**
{watched_intervals_text}

**Evidence Gathered So Far:**
{evidence_summary}

**Current Provisional Answer:**
- Answer: {provisional_answer}
- Confidence: {provisional_confidence:.2f}

---

**Your Task:**
Decide whether the current evidence is actually sufficient to answer the query with high confidence, or whether the system should continue watching the video and gather more evidence.

**Critical Policy:**
- Non-empty evidence does NOT automatically mean the question can be answered.
- If the current evidence mainly shows irrelevant context, pre-event material, missing event context, or otherwise fails to reveal the answer, set `sufficient` to false.
- If the provisional answer says the question cannot yet be determined, keep `sufficient` false unless there is truly no reasonable search left.
- Prefer continued search when confidence is low and unwatched or weakly covered parts of the video remain.
- This reflection must stay generic and mechanism-level. Do NOT tailor the decision to a specific dataset, sport, or named example.

**Search Strategy Guidance:**
- `broaden`: current region was too narrow; inspect a wider temporal area
- `shift`: move to a different temporal area
- `uniform`: use a broader scan to relocate the event
- `refine`: inspect a promising area more precisely
- `stop`: enough evidence exists to answer confidently

**Output Format (STRICT JSON ONLY):**
{json.dumps(REFLECTION_SCHEMA, indent=2)}

Return JSON only, no additional text."""
        return prompt
    
    @staticmethod
    def get_synthesis_prompt(
        original_query: str,
        all_evidence: str,
        video_duration: float,
        options: Optional[List[str]] = None,
        time_base: str = "",
        temporal_hint_summary: str = "",
    ) -> str:
        """Generate prompt for final answer synthesis.
        
        Args:
            original_query: The user's original question
            all_evidence: All evidence collected from all steps
            video_duration: Total video duration
            options: Optional list of multiple choice options (e.g., ["A. Cloudy", "B. Snowy", ...])
                      If None or empty, treated as an open-ended question
        """
        options_list = options if options else []
        
        if len(options_list) > 0:
            options_text = "\n".join([f"  {opt}" for opt in options_list])
            options_section = f"""**Multiple Choice Options:**
{options_text}"""
            task_instruction = "Based on the evidence, select the correct option and explain your reasoning."
            option_instruction = "Choose the option letter (A, B, C, D, etc.) that best answers the question"
            output_schema = MCQ_SCHEMA
            example_block = """**Example Response (with options):**
```json
{
  "selected_option": "B",
  "confidence": 0.95,
  "reasoning": "The evidence shows snow falling and accumulation visible throughout the opening scene. The visual analysis confirms snowy weather conditions with white flakes clearly visible against the background.",
  "selected_option_text": "B. Snowy"
}
```"""
        else:
            options_section = "**Answer Type:**\n  Open-ended question (no multiple-choice options provided)."
            task_instruction = "Based on the evidence, provide a direct open-ended answer to the question."
            option_instruction = "Do not invent option letters. Return a direct answer in the `answer` field."
            output_schema = FINAL_ANSWER_SCHEMA
            example_block = """**Example Response (open-ended):**
```json
{
  "answer": "The person enters the red car at 54 seconds into the video.",
  "key_timestamps": [52, 54],
  "confidence": 0.9,
  "evidence_summary": "Initial scan identified the person approaching the red car around 52s, and the later observation confirmed entry at 54s."
}
```"""
        
        canonical_time_block = """**Canonical Time Reference:**  
All evidence timestamps below are already normalized to `raw_video_seconds`, meaning seconds from the start of the original video.  
When you return `key_timestamps`, use this same canonical timeline only."""
        if time_base == "dataset_reference_only":
            canonical_time_block += (
                "\nDataset-provided annotation times may use a different clock than the raw video, "
                "so trust the normalized evidence timestamps below instead of converting back to annotation reference text."
            )
        elif temporal_hint_summary:
            canonical_time_block += f"\nTemporal note: {temporal_hint_summary}"

        prompt = f"""You are synthesizing the final answer to a question about a video, based on evidence from multiple observation rounds.

**User's Question:** {original_query}

{options_section}

**Video Duration:** {video_duration:.1f} seconds

{canonical_time_block}

**Evidence from All Observation Rounds:**
{all_evidence}

---

**Your Task:**
{task_instruction}

**Guidelines:**
1. **Select Option**: {option_instruction}
2. **Confidence**: Provide your confidence level (0.0 to 1.0)
3. **Supporting Evidence**: Explain how the evidence supports your answer.
4. **Key Timestamps**: Include the most important timestamps that influenced your decision, using canonical `raw_video_seconds`.
5. **Answer Fielding**: For open-ended questions, put the direct answer in `answer`. For MCQ, use `selected_option` and `selected_option_text`.

**Output Format:**
Respond with valid JSON:
{json.dumps(output_schema, indent=2)}

{example_block}

Provide your final answer now in JSON format only."""
    
        return prompt
    
    @staticmethod
    def get_temporal_grounding_planning_prompt(statement: str, video_meta: Dict[str, Any]) -> str:
        """Generate planning prompt for temporal grounding task.
        
        Args:
            statement: Natural language statement to ground temporally
            video_meta: Video metadata (duration)
        """
        duration = (
            video_meta.get("duration_sec")
            or video_meta.get("duration")
            or video_meta.get("video_duration_sec")
            or "unknown"
        )
        
        prompt = f"""You are an expert video analysis planner for temporal grounding. Create a concise, single-action observation plan (ONE step) to locate the temporal region where the given statement occurs.

**Statement to Ground:** {statement}

**Video Information:**
- Duration: {duration} seconds

**Planning Framework:**
Each action must specify three key components:
1. **Goal (Reasoning Objective)**: What you're trying to accomplish
   - Examples: "locate temporal region where statement occurs", "identify precise time boundaries of the event", 
     "find the start and end of the action described in the statement"
   - This should be clearly stated in the "description" field
2. **Region**: The temporal span in the video to examine
   - Can be the whole video (uniform mode) or specific temporal spans (region mode)
   - Specified via "load_mode" ("uniform" for full video, "region" for specific spans)
   - For region mode, provide exact [start, end] timestamps in seconds in the "regions" field
3. **Sampling Granularity**: The fps (frames per second) and resolution settings
   - "fps": Controls temporal sampling rate (0.1-5.0, lower = sparser sampling)
   - "spatial_token_rate": Controls spatial resolution ("low" or "medium", lower = coarser spatial detail)

**Your Planning Strategy:**
1. **Coarse-to-Fine Strategy**: Start with broad uniform scans (low fps, low resolution) to locate candidate regions, then zoom in with higher detail.
2. **Efficiency**: Balance thoroughness with computational cost.

**CRITICAL: SINGLE ACTION REQUIREMENT (THIS ROUND):**
- You MUST output EXACTLY ONE observation action (ONE item in the `steps` array).
- Set the `sub_query` to the EXACT SAME text as the original statement.
- Decide the single action's region/uniform, fps, and spatial_token_rate to best gather evidence for temporal grounding.

**Temporal Grounding Strategy:**
- If the statement describes a specific event/action with no timing hints, start with uniform scan (low fps, low spatial) to locate candidate regions
- If previous rounds found candidate regions, use region mode with higher fps and medium spatial to refine boundaries
- Focus on identifying precise start and end timestamps where the statement is true

**Step Configuration Guidelines (choose ONE for this step):**
- Uniform scan of the full video when timing is unknown
  - load_mode: "uniform"; fps: 0.25–1.0; spatial_token_rate: "low"; regions: []
- Region analysis when candidate regions are known or strongly implied
  - load_mode: "region"; fps: ~2.0; spatial_token_rate: "medium"; regions: [[start, end]]

**Few-Shot Exemplars (JSON):**

- Initial uniform scan (no timing hints):
{{
"reasoning": "Statement describes an event with no timing information. Start with uniform scan to locate candidate regions.",
"steps": [
    {{
    "step_id": "1",
    "description": "Scan entire video to locate temporal region where person enters the red car",
    "sub_query": "The person enters the red car",
    "load_mode": "uniform",
    "fps": 0.5,
    "spatial_token_rate": "low",
    "regions": []
    }}
],
"completion_criteria": "Observation complete when candidate temporal regions are identified"
}}

- Refinement with region mode (after initial scan):
{{
"reasoning": "Previous uniform scan found candidate region around 45-60s. Refine with higher fps and medium spatial to get precise boundaries.",
"steps": [
    {{
    "step_id": "1",
    "description": "Refine temporal boundaries of person entering red car with higher detail",
    "sub_query": "The person enters the red car",
    "load_mode": "region",
    "fps": 2.0,
    "spatial_token_rate": "medium",
    "regions": [[45.0, 60.0]]
    }}
],
"completion_criteria": "Observation complete when precise start and end timestamps are identified"
}}

**Output Format (STRICT JSON ONLY):**
The `steps` array MUST contain exactly ONE item.
Return a single JSON object that validates against this schema:
{json.dumps(PLAN_SCHEMA, indent=2)}

Now generate the plan for temporal grounding. Respond with JSON only, no additional text."""
        
        return prompt
    
    @staticmethod
    def get_temporal_grounding_inference_prompt(
        statement: str,
        context: str,
        start_sec: float,
        end_sec: float,
        video_duration_sec: float = None,
        is_region: bool = False,
        regions: List[Tuple[float, float]] = None
    ) -> str:
        """Generate inference prompt for temporal grounding.
        
        Args:
            statement: Statement to ground temporally
            context: Evidence gathered from previous steps
            start_sec: Start time of video segment
            end_sec: End time of video segment
            video_duration_sec: Total duration of the original video in seconds
            is_region: Whether this is a region/clip (True) or uniform mode (False)
            regions: List of region tuples if multiple regions
        """
        context_text = context if context.strip() else "None (first step)"
        
        # Build video info sentence
        video_info = ""
        if video_duration_sec:
            if is_region and regions and len(regions) > 1:
                video_info = f"**Video Information:** The original video duration is {video_duration_sec:.1f}s. You are analyzing {len(regions)} video segments:\n"
                for i, (reg_start, reg_end) in enumerate(regions, 1):
                    video_info += f"- **Clip {i}**: {reg_start:.1f}s to {reg_end:.1f}s of the original video\n"
                video_info = video_info.rstrip()
            elif is_region:
                video_info = f"**Video Information:** The original video duration is {video_duration_sec:.1f}s. You are analyzing a specific region from {start_sec:.1f}s to {end_sec:.1f}s of the original video."
            else:
                video_info = f"**Video Information:** The video duration is {video_duration_sec:.1f}s. You are analyzing the segment from {start_sec:.1f}s to {end_sec:.1f}s."
        else:
            if is_region and regions and len(regions) > 1:
                video_info = f"**Video Segments:** You are analyzing {len(regions)} video segments:\n"
                for i, (reg_start, reg_end) in enumerate(regions, 1):
                    video_info += f"- **Clip {i}**: {reg_start:.1f}s to {reg_end:.1f}s (duration: {reg_end - reg_start:.1f}s)\n"
                video_info = video_info.rstrip()
            else:
                video_info = f"**Video Segment:** {start_sec:.1f}s to {end_sec:.1f}s (duration: {end_sec - start_sec:.1f}s)"
        
        # Build guidelines section
        guidelines = """- All timestamps must be in seconds from the start of the ORIGINAL video (not relative to this segment)
- Identify timestamp ranges where the statement is TRUE
- Events should be represented as time intervals (timestamp_start, timestamp_end), not single points
- If you see the statement occurring, note the EXACT time range where it occurs
- If you see potential matches, list ALL relevant timestamp ranges
- Be precise with timing - this is critical for temporal grounding
- Consider the context from previous rounds to avoid redundancy
- IMPORTANT: Round intervals to full seconds: floor(timestamp_start), ceil(timestamp_end)"""
        
        # Add guideline for multiple clips if applicable
        if is_region and regions and len(regions) > 1:
            guidelines += "\n- **When analyzing multiple clips**: Each clip corresponds to a specific time range as listed above. When reporting timestamps, always use the ORIGINAL video timestamps (not relative to the clip)."
        
        prompt = f"""You are analyzing a video segment to identify the temporal region where a statement occurs.

**Statement to Ground:** {statement}

{video_info}

**Context from Previous Rounds:**
{context_text}

---

**Your Task:**
Carefully watch the video segment and identify timestamp ranges where the statement is TRUE.

1. **Detailed Observations**: What do you see that relates to the statement?
2. **Key Timestamp Ranges**: For each occurrence of the statement, provide a time interval (start and end timestamps in seconds from video start) where the statement is true
3. **Reasoning**: Explain your observations and findings

**Important Guidelines:**
{guidelines}

**Critical Fallback Strategy:**
- If you're analyzing a REGION (time segment) and you DON'T FIND the statement occurring in this segment, you MUST explicitly state:
  - "No occurrence of the statement found in this time segment"
  - Note that a UNIFORM (full video) scan may be needed to locate the statement
  - Indicate in reasoning that the search should expand to the full video or other regions

**Output Format:**
Respond with valid JSON only:
{json.dumps(EVIDENCE_SCHEMA, indent=2)}

**Example Response:**
```json
{{
  "detailed_response": "A person wearing a red jacket enters the frame from the left side. The individual then walks directly toward a blue sedan parked in the background. At approximately 52 seconds, the person reaches the driver's side door of the blue car, pauses briefly, and then opens the door. The person enters the car and closes the door.",
  "key_evidence": [
    {{"timestamp_start": 50.0, "timestamp_end": 54.0, "description": "Person approaches and opens car door"}},
    {{"timestamp_start": 54.0, "timestamp_end": 56.0, "description": "Person enters the car"}}
  ],
  "reasoning": "The statement 'The person enters the red car' is true from 54.0s to 56.0s. However, I notice the car appears blue, not red. The person clearly enters a car during this time range."
}}
```

Analyze the video now and respond with JSON only."""
        
        return prompt
    
    @staticmethod
    def format_schema_for_api(schema: Dict[str, Any]) -> str:
        """Format schema for inclusion in API request if the model supports schema enforcement."""
        return json.dumps(schema, indent=2)

    @staticmethod
    def get_mcq_prompt(
        question: str,
        options: "list[str]",
        *,
        time_reference: str = "",
        extra_context: str = "",
    ) -> str:
        """Generate prompt for multiple-choice question over a video.

        Args:
            question: The question to answer
            options: A list of option strings in order [A, B, C, ...]
            time_reference: Optional time window string like "00:15-00:19"
            extra_context: Optional textual context
        """
        # Format options for display
        letters = [chr(65 + i) for i in range(len(options))]
        options_lines = "\n".join([f"{letters[i]}. {options[i]}" for i in range(len(options))])

        tr = time_reference.strip()
        tr_line = f"Time Reference: {tr}\n" if tr else ""

        ctx = extra_context.strip() or "None"

        prompt = f"""You are answering a multiple-choice question about a video segment. Carefully analyze the provided video and select the single best option.

Question:
{question}

Options:
{options_lines}

{tr_line}Additional Context:
{ctx}

Instructions:
- Return the option letter only once in the JSON (A/B/C/D/...)
- Consider visual and temporal details in the specified segment if provided
- Provide brief reasoning and a confidence between 0.0 and 1.0

Output Format (JSON only):
{json.dumps(MCQ_SCHEMA, indent=2)}

Example:
```json
{{
  "selected_option": "C",
  "confidence": 0.82,
  "reasoning": "The frame at 16s shows the year clearly as 1633.",
  "selected_option_text": "1633"
}}
```
"""
        return prompt


# ======================================================
# Helper Functions
# ======================================================

def parse_json_response(response_text: str) -> Optional[Dict[str, Any]]:
    """Parse JSON from model response, handling markdown code blocks.
    
    Args:
        response_text: Raw response text from model
        
    Returns:
        Parsed JSON dict, or None if parsing fails
    """
    import re
    
    # Try direct JSON parse first
    try:
        return json.loads(response_text)
    except json.JSONDecodeError:
        pass
    
    def extract_balanced_json_candidates(text: str) -> List[str]:
        candidates: List[str] = []
        for start in range(len(text)):
            if text[start] != "{":
                continue
            depth = 0
            in_string = False
            escape = False
            for end in range(start, len(text)):
                ch = text[end]
                if in_string:
                    if escape:
                        escape = False
                    elif ch == "\\":
                        escape = True
                    elif ch == '"':
                        in_string = False
                    continue
                if ch == '"':
                    in_string = True
                elif ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        candidates.append(text[start:end + 1])
                        break
        return candidates

    # Try to extract JSON from markdown code blocks
    patterns = [
        r'```json\s*\n(.*?)\n```',  # ```json ... ```
        r'```\s*\n(.*?)\n```',       # ``` ... ```
    ]
    
    for pattern in patterns:
        match = re.search(pattern, response_text, re.DOTALL)
        if match:
            try:
                json_str = match.group(1)
                return json.loads(json_str)
            except json.JSONDecodeError:
                continue

    # Try all balanced JSON objects and prefer the largest valid one.
    candidates = sorted(extract_balanced_json_candidates(response_text), key=len, reverse=True)
    for json_str in candidates:
        try:
            return json.loads(json_str)
        except json.JSONDecodeError:
            continue
    
    return None


def validate_against_schema(data: Dict[str, Any], schema: Dict[str, Any]) -> bool:
    """Basic validation of data against schema (checks required fields).
    
    Args:
        data: Data to validate
        schema: JSON schema
        
    Returns:
        True if valid, False otherwise
    """
    if "required" in schema:
        for field in schema["required"]:
            if field not in data:
                return False
    return True
