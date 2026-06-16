from pydantic import BaseModel, Field


class ModelInfo(BaseModel):
    """Selectable LLM model metadata returned to the frontend."""

    id: str
    name: str
    provider: str
    description: str
    enabled: bool = True
    supports_vision: bool = False


class VoiceEngineInfo(BaseModel):
    """Selectable ASR/TTS engine metadata returned to the frontend."""

    id: str
    name: str
    kind: str
    description: str


class ChatMessage(BaseModel):
    """One OpenAI-style chat message."""

    role: str = Field(pattern="^(system|user|assistant)$")
    content: str


class ChatRequest(BaseModel):
    """Chat request shared by normal and streaming endpoints."""

    model: str
    messages: list[ChatMessage]
    attachments: list["AttachmentInfo"] = []
    session_id: int | None = None
    temperature: float = 0.6
    enable_tools: bool = True


class ChatResponse(BaseModel):
    """Full non-streaming chat response."""

    model: str
    provider: str
    reply: str
    session_id: int
    usage: "TokenUsage"
    cost: "CostInfo"
    tools_used: list["ToolResult"] = []


class ToolResult(BaseModel):
    """Structured result from an external tool call."""

    name: str
    title: str
    content: str
    ok: bool = True


class AttachmentInfo(BaseModel):
    """Uploaded file metadata that can be attached to a chat request."""

    id: str
    filename: str
    content_type: str
    url: str
    size: int


class UploadResponse(BaseModel):
    attachments: list[AttachmentInfo]


class SessionCreate(BaseModel):
    title: str | None = None


class ChatSession(BaseModel):
    id: int
    title: str
    created_at: str
    updated_at: str


class ChatHistory(BaseModel):
    session: ChatSession
    messages: list[ChatMessage]


class TokenUsage(BaseModel):
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


class CostInfo(BaseModel):
    currency: str = "USD"
    prompt_cost: float
    completion_cost: float
    total_cost: float


class ModelCallAudit(BaseModel):
    """Public view of one model-call audit row."""

    id: int
    session_id: int
    model: str
    provider: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    total_cost: float
    status: str
    latency_ms: int
    created_at: str


class AsrResponse(BaseModel):
    engine: str
    text: str


class TtsRequest(BaseModel):
    engine: str
    text: str


class TtsResponse(BaseModel):
    engine: str
    text: str
    audio_url: str | None = None
