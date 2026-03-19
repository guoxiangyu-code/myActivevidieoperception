# myActivevidieoperception

A Python project for studying **Agent-based Active Video Understanding** (active video perception).

## Overview

This project explores how AI agents can actively perceive and understand video content. It combines:

- **Active Perception**: Agents that selectively sample and analyze video frames
- **LLM Integration**: Vision-language models for rich video understanding
- **Modular Architecture**: Composable perception and reasoning components

## Project Structure

```
myActivevidieoperception/
├── src/
│   └── agent_video/
│       ├── agents/          # Agent implementations
│       │   ├── base_agent.py
│       │   └── video_agent.py
│       ├── perception/      # Video perception modules
│       │   ├── frame_extractor.py
│       │   └── scene_detector.py
│       └── utils/           # Helper utilities
│           └── video_utils.py
├── examples/
│   └── basic_usage.py       # Quick-start example
├── tests/                   # Unit tests
├── requirements.txt
└── setup.py
```

## Installation

```bash
pip install -e .
```

Or install dependencies directly:

```bash
pip install -r requirements.txt
```

## Quick Start

```python
from agent_video.agents.video_agent import VideoAgent

# Create an agent
agent = VideoAgent(model="gpt-4o")

# Analyze a video
result = agent.analyze("path/to/video.mp4", query="What is happening in this video?")
print(result)
```

See [`examples/basic_usage.py`](examples/basic_usage.py) for more detailed examples.

## Key Concepts

### Active Video Perception

Unlike passive video processing, active perception allows agents to:

1. **Selectively sample frames** based on scene changes and motion
2. **Focus attention** on relevant regions of interest
3. **Iteratively refine** understanding through follow-up queries

### Agent Architecture

```
VideoAgent
  ├── FrameExtractor      → extracts key frames from video
  ├── SceneDetector       → detects scene boundaries and changes
  └── LLM Backend         → reasons over visual content
```

## Running Tests

```bash
python -m pytest tests/ -v
```

## Requirements

- Python 3.9+
- OpenCV (`opencv-python`)
- Pillow
- An OpenAI-compatible API key (for LLM-based agents)

## License

MIT
