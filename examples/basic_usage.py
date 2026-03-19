"""Example: basic usage of the agent_video library."""

from __future__ import annotations

import sys
import os

# Allow running from the repo root without installing the package
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from agent_video.agents.video_agent import VideoAgent


def main() -> None:
    # ---------------------------------------------------------------
    # 1. Create the agent
    # ---------------------------------------------------------------
    agent = VideoAgent(
        model="gpt-4o",
        max_frames=8,
    )
    print(f"Agent: {agent}")

    # ---------------------------------------------------------------
    # 2. Analyze a video
    #    Replace 'sample.mp4' with the path to a real video file.
    # ---------------------------------------------------------------
    video_path = os.path.join(os.path.dirname(__file__), "sample.mp4")

    if not os.path.exists(video_path):
        print(
            f"\n[INFO] Demo video '{video_path}' not found.\n"
            "       Place a video file at that path and re-run this script.\n"
            "       Tip: download a sample with:\n"
            "         curl -L https://www.w3schools.com/html/mov_bbb.mp4 "
            f"-o {video_path}"
        )
        return

    query = "Describe what is happening in this video in 2–3 sentences."
    print(f"\nQuery : {query}")
    print("Analyzing …")

    result = agent.analyze(video_path, query=query)
    print(f"\nAnswer: {result}")


if __name__ == "__main__":
    main()
