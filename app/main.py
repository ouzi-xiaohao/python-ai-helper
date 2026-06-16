"""FastAPI application entrypoint.

This file wires together the HTTP API, static frontend, provider adapters,
session persistence, streaming responses, tool calling, and speech endpoints.
Business logic is deliberately pushed into app/providers.py, app/tools.py,
app/speech.py, app/metrics.py, and app/database.py so routes remain readable.
"""

from pathlib import Path
import json
import time

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from app.config import get_settings
from app.database import (
    add_message,
    create_session,
    get_or_create_session,
    get_session,
    init_db,
    list_audits,
    list_messages,
    list_sessions,
    record_audit,
    replace_session_messages,
)
from app.metrics import estimate_cost, estimate_usage
from app.providers import MODELS, build_providers
from app.schemas import (
    AttachmentInfo,
    AsrResponse,
    ChatHistory,
    ChatMessage,
    ChatRequest,
    ChatResponse,
    ChatSession,
    ModelCallAudit,
    SessionCreate,
    TtsRequest,
    TtsResponse,
    UploadResponse,
)
from app.speech import ASR_ENGINES, TTS_ENGINES, synthesize_speech, transcribe_audio
from app.tools import augment_messages, run_tools
from app.uploads import UPLOAD_DIR, save_upload

BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "static"
AUDIO_DIR = BASE_DIR / "data" / "audio"

app = FastAPI(title="ouzi-AI", version="0.1.0")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
AUDIO_DIR.mkdir(parents=True, exist_ok=True)
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/media/audio", StaticFiles(directory=AUDIO_DIR), name="audio")
app.mount("/media/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")


@app.on_event("startup")
async def startup() -> None:
    init_db()


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/options")
async def options() -> dict[str, object]:
    return {
        "models": [model.model_dump() for model in MODELS],
        "asr_engines": [engine.model_dump() for engine in ASR_ENGINES],
        "tts_engines": [engine.model_dump() for engine in TTS_ENGINES],
    }


def serialize_session(row) -> ChatSession:
    """Convert a sqlite row into a typed response model."""
    return ChatSession(
        id=row["id"],
        title=row["title"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def serialize_audit(row) -> ModelCallAudit:
    """Keep audit serialization in one place for the list endpoint."""
    return ModelCallAudit(
        id=row["id"],
        session_id=row["session_id"],
        model=row["model"],
        provider=row["provider"],
        prompt_tokens=row["prompt_tokens"],
        completion_tokens=row["completion_tokens"],
        total_tokens=row["total_tokens"],
        total_cost=row["total_cost"],
        status=row["status"],
        latency_ms=row["latency_ms"],
        created_at=row["created_at"],
    )


def resolve_model(model_id: str):
    """Find the selected model and its provider adapter."""
    model_info = next((model for model in MODELS if model.id == model_id), None)
    if model_info is None:
        raise HTTPException(status_code=404, detail="Unknown model")

    providers = build_providers(get_settings())
    provider = providers.get(model_info.provider)
    if provider is None:
        raise HTTPException(status_code=404, detail="Unknown provider")
    return model_info, provider


def ensure_vision_supported(
    model_info,
    attachments: list[AttachmentInfo],
) -> None:
    """Reject image requests for text-only models before hitting providers."""
    if attachments and not model_info.supports_vision:
        raise HTTPException(
            status_code=400,
            detail="当前模型不支持图片问答，请选择百炼 Qwen VL Plus 或本地视觉演示模型。",
        )


@app.post("/api/uploads", response_model=UploadResponse)
async def upload_files(files: list[UploadFile] = File(...)) -> UploadResponse:
    """Save image files and return metadata for later chat requests."""
    attachments: list[AttachmentInfo] = []
    for file in files:
        try:
            attachments.append(await save_upload(file))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    return UploadResponse(attachments=attachments)


@app.post("/api/sessions", response_model=ChatSession)
async def create_chat_session(request: SessionCreate) -> ChatSession:
    return serialize_session(create_session(request.title))


@app.get("/api/sessions", response_model=list[ChatSession])
async def sessions() -> list[ChatSession]:
    return [serialize_session(row) for row in list_sessions()]


@app.get("/api/sessions/{session_id}", response_model=ChatHistory)
async def history(session_id: int) -> ChatHistory:
    session = get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    messages = [
        ChatMessage(role=row["role"], content=row["content"])
        for row in list_messages(session_id)
    ]
    return ChatHistory(session=serialize_session(session), messages=messages)


@app.get("/api/audits", response_model=list[ModelCallAudit])
async def audits(limit: int = 100) -> list[ModelCallAudit]:
    return [serialize_audit(row) for row in list_audits(limit)]


@app.post("/api/chat", response_model=ChatResponse)
async def chat(request: ChatRequest) -> ChatResponse:
    """Non-streaming chat endpoint.

    The route executes tools first, augments the prompt, calls the model, then
    persists history and audit metrics in SQLite.
    """
    model_info, provider = resolve_model(request.model)
    ensure_vision_supported(model_info, request.attachments)
    session = get_or_create_session(request.session_id)
    session_id = session["id"]
    start = time.perf_counter()
    settings = get_settings()
    tool_results = await run_tools(request.messages, settings) if request.enable_tools else []
    model_messages = augment_messages(request.messages, tool_results)

    try:
        reply = await provider.chat(
            request.model,
            model_messages,
            request.temperature,
            request.attachments,
        )
    except Exception as exc:
        latency_ms = round((time.perf_counter() - start) * 1000)
        record_audit(
            session_id=session_id,
            model=request.model,
            provider=model_info.provider,
            prompt_tokens=0,
            completion_tokens=0,
            total_tokens=0,
            prompt_cost=0,
            completion_cost=0,
            total_cost=0,
            status="failed",
            latency_ms=latency_ms,
            error=str(exc),
        )
        raise HTTPException(status_code=502, detail=f"Provider error: {exc}") from exc

    latency_ms = round((time.perf_counter() - start) * 1000)
    usage = estimate_usage(model_messages, reply)
    cost = estimate_cost(request.model, usage)
    replace_session_messages(session_id, request.messages)
    add_message(session_id, "assistant", reply)
    record_audit(
        session_id=session_id,
        model=request.model,
        provider=model_info.provider,
        prompt_tokens=usage.prompt_tokens,
        completion_tokens=usage.completion_tokens,
        total_tokens=usage.total_tokens,
        prompt_cost=cost.prompt_cost,
        completion_cost=cost.completion_cost,
        total_cost=cost.total_cost,
        status="success",
        latency_ms=latency_ms,
    )

    return ChatResponse(
        model=request.model,
        provider=model_info.provider,
        reply=reply,
        session_id=session_id,
        usage=usage,
        cost=cost,
        tools_used=tool_results,
    )


@app.post("/api/chat/stream")
async def stream_chat(request: ChatRequest) -> StreamingResponse:
    """Streaming chat endpoint using Server-Sent Events.

    Event types:
    - meta: session metadata
    - tool: each external tool result
    - token: incremental model output
    - done: usage and cost summary
    - error: provider/tool failure
    """
    model_info, provider = resolve_model(request.model)
    ensure_vision_supported(model_info, request.attachments)
    session = get_or_create_session(request.session_id)
    session_id = session["id"]
    settings = get_settings()

    async def events():
        start = time.perf_counter()
        chunks: list[str] = []
        yield f"event: meta\ndata: {json.dumps({'session_id': session_id}, ensure_ascii=False)}\n\n"
        try:
            tool_results = await run_tools(request.messages, settings) if request.enable_tools else []
            for result in tool_results:
                yield (
                    "event: tool\n"
                    f"data: {json.dumps(result.model_dump(), ensure_ascii=False)}\n\n"
                )
            model_messages = augment_messages(request.messages, tool_results)
            async for chunk in provider.stream_chat(
                request.model,
                model_messages,
                request.temperature,
                request.attachments,
            ):
                chunks.append(chunk)
                yield f"event: token\ndata: {json.dumps({'text': chunk}, ensure_ascii=False)}\n\n"

            reply = "".join(chunks)
            latency_ms = round((time.perf_counter() - start) * 1000)
            usage = estimate_usage(model_messages, reply)
            cost = estimate_cost(request.model, usage)
            replace_session_messages(session_id, request.messages)
            add_message(session_id, "assistant", reply)
            record_audit(
                session_id=session_id,
                model=request.model,
                provider=model_info.provider,
                prompt_tokens=usage.prompt_tokens,
                completion_tokens=usage.completion_tokens,
                total_tokens=usage.total_tokens,
                prompt_cost=cost.prompt_cost,
                completion_cost=cost.completion_cost,
                total_cost=cost.total_cost,
                status="success",
                latency_ms=latency_ms,
            )
            done = {
                "session_id": session_id,
                "usage": usage.model_dump(),
                "cost": cost.model_dump(),
                "tools_used": [result.model_dump() for result in tool_results],
            }
            yield f"event: done\ndata: {json.dumps(done, ensure_ascii=False)}\n\n"
        except Exception as exc:
            latency_ms = round((time.perf_counter() - start) * 1000)
            record_audit(
                session_id=session_id,
                model=request.model,
                provider=model_info.provider,
                prompt_tokens=0,
                completion_tokens=0,
                total_tokens=0,
                prompt_cost=0,
                completion_cost=0,
                total_cost=0,
                status="failed",
                latency_ms=latency_ms,
                error=str(exc),
            )
            error = {"message": str(exc)}
            yield f"event: error\ndata: {json.dumps(error, ensure_ascii=False)}\n\n"

    return StreamingResponse(events(), media_type="text/event-stream")


@app.post("/api/asr", response_model=AsrResponse)
async def asr(
    engine: str = Form(...),
    audio: UploadFile | None = File(default=None),
) -> AsrResponse:
    audio_bytes = await audio.read() if audio else None
    try:
        text = await transcribe_audio(
            engine,
            audio.filename if audio else None,
            audio_bytes,
            audio.content_type if audio else None,
            get_settings(),
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"ASR service error: {exc}") from exc
    return AsrResponse(engine=engine, text=text)


@app.post("/api/tts", response_model=TtsResponse)
async def tts(request: TtsRequest) -> TtsResponse:
    try:
        audio_url = await synthesize_speech(request.engine, request.text, get_settings())
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"TTS service error: {exc}") from exc
    return TtsResponse(engine=request.engine, text=request.text, audio_url=audio_url)
