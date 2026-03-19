"""Package setup for agent_video."""

from setuptools import setup, find_packages

setup(
    name="agent-video",
    version="0.1.0",
    description="Agent-based Active Video Understanding",
    long_description=open("README.md").read(),
    long_description_content_type="text/markdown",
    author="guoxiangyu-code",
    python_requires=">=3.9",
    package_dir={"": "src"},
    packages=find_packages(where="src"),
    install_requires=[
        "opencv-python>=4.8.0",
        "numpy>=1.24.0",
        "Pillow>=10.0.0",
    ],
    extras_require={
        "llm": ["openai>=1.0.0"],
        "dev": ["pytest>=7.0.0"],
    },
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
        "Topic :: Scientific/Engineering :: Artificial Intelligence",
        "Topic :: Multimedia :: Video",
    ],
)
