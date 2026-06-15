#!/usr/bin/env python3
"""
小艺 Claw 系统 — 核心能力引擎 Python 包

OpenClaw 的开源增强能力层，为 AI Assistant 提供记忆、检索、推理、验证、自进化等全套认知能力。

安装:
    pip install -e .           # 开发模式
    pip install -r requirements.txt  # 仅安装依赖
"""
from setuptools import setup, find_packages

setup(
    name="galaxyos",
    version="8.2.9",

    description="GalaxyOS — 小艺 Claw 核心认知增强引擎 (unified package)",
    long_description=open("README.md", encoding="utf-8").read(),
    long_description_content_type="text/markdown",
    author="xiaoyi-claw",
    author_email="dev@xiaoyi-claw.ai",
    url="https://cnb.cool/llm-memory-integrat/GalaxyOS",
    packages=find_packages(include=["galaxyos", "galaxyos.*"]),
    include_package_data=True,
    python_requires=">=3.12",
    install_requires=[
        # 核心库
        "numpy==2.4.5",
        "mkl==2026.0.0",
        "tbb==2023.0.0",
        # 进程间通信
        "pyzmq>=25.0.0",
        # 网络
        "aiohttp>=3.9.0",
        "httpx>=0.27.0",
        "requests>=2.31.0",
        # 向量检索
        "faiss-cpu>=1.7.0",
        "hnswlib==0.8.0",
        # 图像
        "Pillow>=10.0.0",
        # 序列化
        "orjson>=3.9.0",
        # 数据处理
        "polars>=1.0.0",
        "duckdb>=1.0.0",
        "pandas>=2.0.0",
        # 中文 NLP
        "jieba>=0.42.0",
        "snownlp>=0.12.0",
        "tiktoken>=0.5.0",
        # 异步
        "uvloop>=0.19.0",
        # ML/DL
        "scikit-learn>=1.3.0",
        "onnxruntime>=1.15.0",
        "torch>=2.0.0",
        # 系统
        "psutil>=5.9.0",
        # LLM API 调用
        "openai>=1.0.0",
        # 神经电路策略 (NCPs)
        "ncps>=1.0.0",
        # 数据校验
        "pydantic>=2.0.0",
        # 图神经网络
        "scipy>=1.10.0",
    ],
    extras_require={
        "torch": [
            "torch>=2.0.0",
            "ncps>=1.0.0",
        ],
        "gpu": [
            "torch>=2.0.0",
            "pyopencl>=2023.0",
        ],
        "dev": [
            "pytest>=7.0.0",
            "black>=23.0.0",
        ],
    },
    classifiers=[
        "Development Status :: 4 - Beta",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: MIT License",
        "Operating System :: POSIX :: Linux",
        "Programming Language :: Python :: 3.12",
        "Topic :: Scientific/Engineering :: Artificial Intelligence",
    ],
)
