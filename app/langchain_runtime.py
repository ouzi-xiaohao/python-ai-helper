from __future__ import annotations

"""LangChain-backed chat runtimes.

The rest of the project still speaks in our own request/response schema, but
actual model invocation now goes through LangChain chat models. This keeps the
FastAPI and frontend surface stable while moving orchestration onto LangChain.
"""

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from app.config import Settings
from app.schemas import AttachmentInfo, ChatMessage
from app.uploads import attachment_to_data_url, describe_attachments


class ChatRuntime(ABC):
    """Small runtime contract used by FastAPI routes."""

    provider_id: str
    provider_name: str

    @abstractmethod
    async def ainvoke(
        self,
        model: str,
        messages: list[ChatMessage],
        temperature: float,
        attachments: list[AttachmentInfo] | None = None,
    ) -> str:
        raise NotImplementedError

    async def astream(
        self,
        model: str,
        messages: list[ChatMessage],
        temperature: float,
        attachments: list[AttachmentInfo] | None = None,
    ) -> AsyncIterator[str]:
        reply = await self.ainvoke(model, messages, temperature, attachments)
        for index in range(0, len(reply), 8):
            yield reply[index : index + 8]


class LocalDemoRuntime(ChatRuntime):
    """Demo runtime kept for local testing and key-less flows."""

    provider_id = "demo"
    provider_name = "本地演示"

    async def ainvoke(
        self,
        model: str,
        messages: list[ChatMessage],
        temperature: float,
        attachments: list[AttachmentInfo] | None = None,
    ) -> str:
        del temperature
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
        if "知识库" in tool_context:
            knowledge_start = tool_context.find("以下是从本地知识库检索到的资料片段")
            knowledge_context = tool_context[knowledge_start:] if knowledge_start >= 0 else tool_context
            useful_lines = [
                line.strip()
                for line in knowledge_context.splitlines()
                if line.strip()
                and not line.startswith("以下是")
                and not line.startswith("来源：")
                and line.strip() != "---"
            ]
            excerpt = "\n".join(useful_lines[:6])
            return f"根据知识库资料，可以这样处理：\n{excerpt}"
        if any(keyword in last_user for keyword in ("报修", "故障", "维修", "坏了", "不制冷")):
            return (
                "可以，我先按校园服务台报修流程帮你整理：\n\n"
                "报修类型：宿舍/设备故障\n"
                "需要补充：楼栋房间、设备名称、故障现象、是否影响安全、联系方式。\n"
                "可提交描述：设备出现异常，请后勤尽快检查处理。"
                "如果你上传现场图片，我可以继续帮你生成更完整的报修说明。"
            )
        if any(keyword in last_user for keyword in ("学生证", "补办", "材料", "办理", "流程")):
            return (
                "可以按办事咨询处理。建议准备：本人身份证明、学生信息、遗失说明或申请表。"
                "办理步骤通常是：确认学院/部门要求，填写申请，提交材料，等待审核领取。"
                "不同学校规则可能不同，最终以本校办事大厅通知为准。"
            )
        if "天气" in last_user:
            return "今天北京天气晴朗，气温25°C，适合户外活动。"
        if "你好" in last_user or "您好" in last_user:
            return "你好！我是校园服务智能助手，可以帮你做办事咨询、宿舍报修、图片故障描述和实时信息查询。"
        return f"我已收到你的问题：{last_user}。当前使用的是 {model} 演示通道，接入密钥后即可调用真实模型。"


class OpenAICompatibleLangChainRuntime(ChatRuntime):
    """LangChain runtime for OpenAI-compatible providers."""

    def __init__(
        self,
        *,
        provider_id: str,
        provider_name: str,
        api_key: str | None,
        base_url: str,
        model_override: str | None = None,
        missing_model_message: str | None = None,
    ) -> None:
        self.provider_id = provider_id
        self.provider_name = provider_name
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model_override = model_override
        self.missing_model_message = missing_model_message
        self.fallback = LocalDemoRuntime()

    def _build_llm(self, model: str, temperature: float) -> ChatOpenAI:
        if self.missing_model_message and not self.model_override:
            raise RuntimeError(self.missing_model_message)
        return ChatOpenAI(
            model=self.model_override or model,
            api_key=self.api_key,
            base_url=self.base_url,
            temperature=temperature,
            streaming=True,
        )

    async def ainvoke(
        self,
        model: str,
        messages: list[ChatMessage],
        temperature: float,
        attachments: list[AttachmentInfo] | None = None,
    ) -> str:
        if not self.api_key:
            return await self.fallback.ainvoke(model, messages, temperature, attachments)

        llm = self._build_llm(model, temperature)
        response = await llm.ainvoke(to_langchain_messages(messages, attachments or []))
        if isinstance(response.content, str):
            return response.content
        return flatten_message_content(response.content)

    async def astream(
        self,
        model: str,
        messages: list[ChatMessage],
        temperature: float,
        attachments: list[AttachmentInfo] | None = None,
    ) -> AsyncIterator[str]:
        if not self.api_key:
            async for chunk in self.fallback.astream(model, messages, temperature, attachments):
                yield chunk
            return

        llm = self._build_llm(model, temperature)
        async for chunk in llm.astream(to_langchain_messages(messages, attachments or [])):
            text = flatten_message_content(chunk.content)
            if text:
                yield text


def flatten_message_content(content: object) -> str:
    """Normalize LangChain/OpenAI message content into plain text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                if item.get("type") == "text":
                    parts.append(str(item.get("text", "")))
        return "".join(parts)
    return str(content or "")


def to_langchain_messages(
    messages: list[ChatMessage],
    attachments: list[AttachmentInfo],
) -> list[BaseMessage]:
    """Convert application messages to LangChain message objects."""
    lc_messages: list[BaseMessage] = []
    attachment_parts = [
        {"type": "image_url", "image_url": {"url": attachment_to_data_url(attachment)}}
        for attachment in attachments
    ]

    for index, message in enumerate(messages):
        if message.role == "system":
            lc_messages.append(SystemMessage(content=message.content))
            continue
        if message.role == "assistant":
            lc_messages.append(AIMessage(content=message.content))
            continue

        is_last_user = index == max(
            (i for i, item in enumerate(messages) if item.role == "user"),
            default=-1,
        )
        if attachment_parts and is_last_user:
            content: list[dict[str, object]] = []
            if message.content:
                content.append({"type": "text", "text": message.content})
            content.extend(attachment_parts)
            lc_messages.append(HumanMessage(content=content))
        else:
            lc_messages.append(HumanMessage(content=message.content))
    return lc_messages


def build_runtimes(settings: Settings) -> dict[str, ChatRuntime]:
    """Create provider runtimes from environment-backed settings."""
    return {
        "demo": LocalDemoRuntime(),
        "deepseek": OpenAICompatibleLangChainRuntime(
            provider_id="deepseek",
            provider_name="DeepSeek",
            api_key=settings.deepseek_api_key,
            base_url="https://api.deepseek.com",
        ),
        "doubao": OpenAICompatibleLangChainRuntime(
            provider_id="doubao",
            provider_name="豆包",
            api_key=settings.doubao_api_key,
            base_url="https://ark.cn-beijing.volces.com/api/v3",
            model_override=settings.doubao_model_id,
            missing_model_message=(
                "豆包需要配置火山方舟控制台里的推理接入点 ID。"
                "请在 .env 中设置 DOUBAO_MODEL_ID=ep-xxxxxxxx，"
                "它不是展示名称 doubao-pro-32k。"
            ),
        ),
        "bailian": OpenAICompatibleLangChainRuntime(
            provider_id="bailian",
            provider_name="百炼",
            api_key=settings.bailian_api_key,
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        ),
    }
