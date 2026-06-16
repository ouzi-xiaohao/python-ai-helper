from __future__ import annotations

"""LLM provider adapters.

Every model provider implements the same small interface: full-response chat
and streaming chat. The API layer can therefore switch between DeepSeek,
Doubao, Bailian, and the local demo model without knowing provider details.
"""

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator

import httpx

from app.config import Settings
from app.schemas import AttachmentInfo, ChatMessage, ModelInfo
from app.uploads import attachment_to_data_url, describe_attachments


class ChatProvider(ABC):
    """Base contract for all chat providers."""

    id: str
    name: str

    @abstractmethod
    async def chat(
        self,
        model: str,
        messages: list[ChatMessage],
        temperature: float,
        attachments: list[AttachmentInfo] | None = None,
    ) -> str:
        raise NotImplementedError

    async def stream_chat(
        self,
        model: str,
        messages: list[ChatMessage],
        temperature: float,
        attachments: list[AttachmentInfo] | None = None,
    ) -> AsyncIterator[str]:
        # Providers that do not support native streaming can still participate
        # by chunking a normal response. Real providers may override this.
        reply = await self.chat(model, messages, temperature, attachments)
        for index in range(0, len(reply), 8):
            yield reply[index : index + 8]


class DemoProvider(ChatProvider):
    """Local provider used for demos and missing API keys."""

    id = "demo"
    name = "本地演示"

    async def chat(
        self,
        model: str,
        messages: list[ChatMessage],
        temperature: float,
        attachments: list[AttachmentInfo] | None = None,
    ) -> str:
        last_user = next(
            (message.content for message in reversed(messages) if message.role == "user"),
            "",
        )
        tool_context = "\n".join(
            message.content for message in messages if message.role == "system"
        )
        if attachments:
            return (
                "我已收到图片，但当前使用的是本地视觉演示通道，无法真正识别画面内容。"
                "已接入真实视觉模型时，系统会把图片以多模态格式发送给模型。\n\n"
                f"图片信息：\n{describe_attachments(attachments)}"
            )
        if "实时天气" in tool_context and "天气" in last_user:
            lines = [
                line
                for line in tool_context.splitlines()
                if "实时天气：" in line or "天气工具暂时不可用" in line
            ]
            return lines[0] if lines else "我尝试查询实时天气，但暂时没有拿到结果。"
        if "当前北京时间" in tool_context and any(
            keyword in last_user.lower()
            for keyword in ("今天", "几号", "日期", "星期", "时间", "today", "date", "time")
        ):
            marker = "当前北京时间："
            start = tool_context.find(marker)
            end = tool_context.find("。", start)
            current_time = tool_context[start + len(marker) : end] if start >= 0 else "当前时间未知"
            return f"根据实时工具结果，现在是北京时间 {current_time}。"
        if "联网搜索" in tool_context and any(
            keyword in last_user for keyword in ("新闻", "最新", "搜索", "查一下", "联网")
        ):
            return f"我已调用联网搜索工具，结果如下：\n{tool_context}"
        if "天气" in last_user:
            return "今天北京天气晴朗，气温25°C，适合户外活动。"
        if "你好" in last_user or "您好" in last_user:
            return "你好！我是你的AI助手，请选择模型和语音引擎，然后点击麦克风开始对话。"
        return f"我已收到你的问题：{last_user}。当前使用的是 {model} 演示通道，接入密钥后即可调用真实模型。"


class OpenAICompatibleProvider(ChatProvider):
    """Adapter for providers exposing an OpenAI-compatible chat API."""

    def __init__(
        self,
        *,
        provider_id: str,
        name: str,
        api_key: str | None,
        base_url: str,
        model_override: str | None = None,
        missing_model_message: str | None = None,
    ) -> None:
        self.id = provider_id
        self.name = name
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model_override = model_override
        self.missing_model_message = missing_model_message
        self.fallback = DemoProvider()

    async def chat(
        self,
        model: str,
        messages: list[ChatMessage],
        temperature: float,
        attachments: list[AttachmentInfo] | None = None,
    ) -> str:
        if not self.api_key:
            # No key configured: keep the UI usable by falling back to demo.
            return await self.fallback.chat(model, messages, temperature, attachments)
        if self.missing_model_message and not self.model_override:
            raise RuntimeError(self.missing_model_message)

        payload = {
            "model": self.model_override or model,
            "messages": build_openai_messages(messages, attachments or []),
            "temperature": temperature,
        }
        headers = {"Authorization": f"Bearer {self.api_key}"}
        async with httpx.AsyncClient(timeout=45) as client:
            response = await client.post(
                f"{self.base_url}/chat/completions",
                headers=headers,
                json=payload,
            )
            response.raise_for_status()
            data = response.json()
        return data["choices"][0]["message"]["content"]

    async def stream_chat(
        self,
        model: str,
        messages: list[ChatMessage],
        temperature: float,
        attachments: list[AttachmentInfo] | None = None,
    ) -> AsyncIterator[str]:
        if not self.api_key:
            # Streaming fallback mirrors the full-response fallback above.
            async for chunk in self.fallback.stream_chat(
                model,
                messages,
                temperature,
                attachments,
            ):
                yield chunk
            return
        if self.missing_model_message and not self.model_override:
            raise RuntimeError(self.missing_model_message)

        payload = {
            "model": self.model_override or model,
            "messages": build_openai_messages(messages, attachments or []),
            "temperature": temperature,
            "stream": True,
        }
        headers = {"Authorization": f"Bearer {self.api_key}"}
        async with httpx.AsyncClient(timeout=60) as client:
            async with client.stream(
                "POST",
                f"{self.base_url}/chat/completions",
                headers=headers,
                json=payload,
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    data = line.removeprefix("data: ").strip()
                    if data == "[DONE]":
                        break
                    event = httpx.Response(200, content=data).json()
                    delta = event["choices"][0].get("delta", {})
                    content = delta.get("content")
                    if content:
                        yield content


def build_openai_messages(
    messages: list[ChatMessage],
    attachments: list[AttachmentInfo],
) -> list[dict[str, object]]:
    """Convert internal messages to OpenAI-compatible payload messages.

    When images are attached, they are added to the latest user message using
    the standard content-part shape: text plus image_url entries.
    """
    payload = [message.model_dump() for message in messages]
    if not attachments:
        return payload

    for message in reversed(payload):
        if message["role"] != "user":
            continue
        text = str(message.get("content") or "")
        content_parts: list[dict[str, object]] = []
        if text:
            content_parts.append({"type": "text", "text": text})
        for attachment in attachments:
            content_parts.append(
                {
                    "type": "image_url",
                    "image_url": {"url": attachment_to_data_url(attachment)},
                }
            )
        message["content"] = content_parts
        break
    return payload


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


def build_providers(settings: Settings) -> dict[str, ChatProvider]:
    return {
        "demo": DemoProvider(),
        "deepseek": OpenAICompatibleProvider(
            provider_id="deepseek",
            name="DeepSeek",
            api_key=settings.deepseek_api_key,
            base_url="https://api.deepseek.com",
        ),
        "doubao": OpenAICompatibleProvider(
            provider_id="doubao",
            name="豆包",
            api_key=settings.doubao_api_key,
            base_url="https://ark.cn-beijing.volces.com/api/v3",
            model_override=settings.doubao_model_id,
            missing_model_message=(
                "豆包需要配置火山方舟控制台里的推理接入点 ID。"
                "请在 .env 中设置 DOUBAO_MODEL_ID=ep-xxxxxxxx，"
                "它不是展示名称 doubao-pro-32k。"
            ),
        ),
        "bailian": OpenAICompatibleProvider(
            provider_id="bailian",
            name="百炼",
            api_key=settings.bailian_api_key,
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        ),
    }
