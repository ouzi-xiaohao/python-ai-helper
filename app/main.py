"""FastAPI application entrypoint.

This file wires together the HTTP API, LangChain runtimes, session
persistence, streaming responses, tool calling, and speech endpoints.
Business logic is deliberately pushed into app/langchain_runtime.py,
app/providers.py, app/tools.py, app/speech.py, app/metrics.py,
and app/database.py so routes remain readable.
"""

from pathlib import Path
import json
import time

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from app.auth import authenticate_user, create_access_token, ensure_default_admin, require_admin, require_user
from app.config import get_settings
from app.database import (
    add_message,
    create_session,
    get_or_create_session,
    get_session,
    init_db,
    list_audits,
    list_knowledge_documents,
    list_messages,
    list_sessions,
    record_audit,
    replace_session_messages,
)
from app.metrics import estimate_cost, estimate_usage
from app.langchain_runtime import build_runtimes
from app.knowledge import ingest_knowledge_file, retrieve_knowledge_result
from app.providers import MODELS
from app.scenario import apply_scenario_prompt, get_scenario_options
from app.schemas import (
    AttachmentInfo,
    AuthResponse,
    AsrResponse,
    ChatHistory,
    ChatMessage,
    ChatRequest,
    ChatResponse,
    ChatSession,
    CurrentUser,
    KnowledgeDocument,
    KnowledgeUploadResponse,
    LoginRequest,
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

app = FastAPI(title="校园服务智能助手", version="0.1.0")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
AUDIO_DIR.mkdir(parents=True, exist_ok=True)
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/media/audio", StaticFiles(directory=AUDIO_DIR), name="audio")
app.mount("/media/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")


@app.on_event("startup")
async def startup() -> None:
    init_db()
    ensure_default_admin(get_settings())


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.post("/api/login", response_model=AuthResponse)
async def login(request: LoginRequest) -> AuthResponse:
    user = authenticate_user(request.username, request.password)
    if user is None:
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    token = create_access_token(user)
    return AuthResponse(access_token=token, username=user.username, role=user.role)


@app.get("/api/me", response_model=CurrentUser)
async def me(user: CurrentUser = Depends(require_user)) -> CurrentUser:
    return user


@app.get("/api/options")
async def options(_user: CurrentUser = Depends(require_user)) -> dict[str, object]:
    return {
        "models": [model.model_dump() for model in MODELS],
        "asr_engines": [engine.model_dump() for engine in ASR_ENGINES],
        "tts_engines": [engine.model_dump() for engine in TTS_ENGINES],
        "scenario": get_scenario_options(),
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


def serialize_knowledge_document(row) -> KnowledgeDocument:
    """Convert a knowledge document sqlite row into API metadata."""
    return KnowledgeDocument(
        id=row["id"],
        filename=row["filename"],
        content_type=row["content_type"],
        chunk_count=row["chunk_count"],
        created_at=row["created_at"],
    )


def resolve_model(model_id: str):
    """Find the selected model and its LangChain-backed runtime."""
    model_info = next((model for model in MODELS if model.id == model_id), None)
    if model_info is None:
        raise HTTPException(status_code=404, detail="Unknown model")

    runtimes = build_runtimes(get_settings())
    runtime = runtimes.get(model_info.provider)
    if runtime is None:
        raise HTTPException(status_code=404, detail="Unknown provider")
    return model_info, runtime


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
async def upload_files(
    files: list[UploadFile] = File(...),
    _user: CurrentUser = Depends(require_user),
) -> UploadResponse:
    """Save image files and return metadata for later chat requests."""
    attachments: list[AttachmentInfo] = []
    for file in files:
        try:
            attachments.append(await save_upload(file))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    return UploadResponse(attachments=attachments)


@app.post("/api/knowledge/upload", response_model=KnowledgeUploadResponse)
async def upload_knowledge_file(
    file: UploadFile = File(...),
    _user: CurrentUser = Depends(require_admin),
) -> KnowledgeUploadResponse:
    """Ingest one document into the local knowledge base."""
    try:
        document = await ingest_knowledge_file(file)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return KnowledgeUploadResponse(document=document)


@app.get("/api/knowledge", response_model=list[KnowledgeDocument])
async def knowledge_documents(_user: CurrentUser = Depends(require_user)) -> list[KnowledgeDocument]:
    return [serialize_knowledge_document(row) for row in list_knowledge_documents()]


@app.post("/api/sessions", response_model=ChatSession)
async def create_chat_session(
    request: SessionCreate,
    _user: CurrentUser = Depends(require_user),
) -> ChatSession:
    return serialize_session(create_session(request.title))


@app.get("/api/sessions", response_model=list[ChatSession])
async def sessions(_user: CurrentUser = Depends(require_user)) -> list[ChatSession]:
    return [serialize_session(row) for row in list_sessions()]


@app.get("/api/sessions/{session_id}", response_model=ChatHistory)
async def history(
    session_id: int,
    _user: CurrentUser = Depends(require_user),
) -> ChatHistory:
    session = get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    messages = [
        ChatMessage(role=row["role"], content=row["content"])
        for row in list_messages(session_id)
    ]
    return ChatHistory(session=serialize_session(session), messages=messages)


@app.get("/api/audits", response_model=list[ModelCallAudit])
async def audits(
    limit: int = 100,
    _user: CurrentUser = Depends(require_admin),
) -> list[ModelCallAudit]:
    return [serialize_audit(row) for row in list_audits(limit)]


@app.post("/api/chat", response_model=ChatResponse)
async def chat(
    request: ChatRequest,
    _user: CurrentUser = Depends(require_user),
) -> ChatResponse:
    """Non-streaming chat endpoint.

    The route executes tools first, augments the prompt, calls the model, then
    persists history and audit metrics in SQLite.
    """
    model_info, runtime = resolve_model(request.model)
    ensure_vision_supported(model_info, request.attachments)
    session = get_or_create_session(request.session_id)
    session_id = session["id"]
    start = time.perf_counter()
    settings = get_settings()
    tool_results = await run_tools(request.messages, settings) if request.enable_tools else []
    knowledge_result = retrieve_knowledge_result(request.messages)
    if knowledge_result:
        tool_results.insert(0, knowledge_result)
    model_messages = apply_scenario_prompt(augment_messages(request.messages, tool_results))

    try:
        reply = await runtime.ainvoke(
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
async def stream_chat(
    request: ChatRequest,
    _user: CurrentUser = Depends(require_user),
) -> StreamingResponse:
    """Streaming chat endpoint using Server-Sent Events.

    Event types:
    - meta: session metadata
    - tool: each external tool result
    - token: incremental model output
    - done: usage and cost summary
    - error: provider/tool failure
    """
    model_info, runtime = resolve_model(request.model)
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
            knowledge_result = retrieve_knowledge_result(request.messages)
            if knowledge_result:
                tool_results.insert(0, knowledge_result)
            for result in tool_results:
                yield (
                    "event: tool\n"
                    f"data: {json.dumps(result.model_dump(), ensure_ascii=False)}\n\n"
                )
            model_messages = apply_scenario_prompt(augment_messages(request.messages, tool_results))
            async for chunk in runtime.astream(
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
    _user: CurrentUser = Depends(require_user),
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
async def tts(
    request: TtsRequest,
    _user: CurrentUser = Depends(require_user),
) -> TtsResponse:
    try:
        audio_url = await synthesize_speech(request.engine, request.text, get_settings())
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"TTS service error: {exc}") from exc
    return TtsResponse(engine=request.engine, text=request.text, audio_url=audio_url)
