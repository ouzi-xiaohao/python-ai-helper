from __future__ import annotations

"""Model catalog for the UI and routing layer.

LangChain handles the runtime invocation layer. This module now focuses on
describing which models exist, who provides them, and whether they support
vision, while runtime construction lives in app/langchain_runtime.py.
"""

from app.schemas import ModelInfo


MODELS = [
    ModelInfo(
        id="deepseek-chat",
        name="DeepSeek-V3",
        provider="deepseek",
        description="DeepSeek 通用对话模型",
    ),
    ModelInfo(
        id="doubao-pro-32k",
        name="豆包 Pro 32K",
        provider="doubao",
        description="火山方舟/豆包长上下文对话模型",
    ),
    ModelInfo(
        id="qwen-plus",
        name="百炼 Qwen Plus",
        provider="bailian",
        description="阿里云百炼通义千问模型",
    ),
    ModelInfo(
        id="qwen-vl-plus",
        name="百炼 Qwen VL Plus",
        provider="bailian",
        description="阿里云百炼视觉问答模型",
        supports_vision=True,
    ),
    ModelInfo(
        id="aethervoice-demo",
        name="本地演示模型",
        provider="demo",
        description="无需密钥的本地演示响应",
    ),
    ModelInfo(
        id="aethervoice-vision-demo",
        name="本地视觉演示模型",
        provider="demo",
        description="无需密钥的图片上传流程演示",
        supports_vision=True,
    ),
]
