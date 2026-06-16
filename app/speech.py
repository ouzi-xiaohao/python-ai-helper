from __future__ import annotations

"""Speech engine adapters for ASR and TTS.

The public functions at the bottom are called by FastAPI routes. Provider
specific code stays here so the API layer does not need to know whether an
engine is browser-based, demo-only, or a remote cloud service such as Baidu.
"""

import base64
import time
from pathlib import Path

import httpx

from app.config import Settings
from app.schemas import VoiceEngineInfo

BASE_DIR = Path(__file__).resolve().parent.parent
AUDIO_DIR = BASE_DIR / "data" / "audio"

# Access tokens are short-lived. A tiny in-memory cache avoids requesting a new
# Baidu token on every ASR/TTS call during local development.
_baidu_token_cache: dict[str, object] = {"token": None, "expires_at": 0.0}


ASR_ENGINES = [
    VoiceEngineInfo(
        id="huoshan-asr",
        name="火山引擎 ASR",
        kind="asr",
        description="预留火山引擎语音识别接口",
    ),
    VoiceEngineInfo(
        id="browser-asr",
        name="浏览器语音识别",
        kind="asr",
        description="使用浏览器 Web Speech API 的前端识别",
    ),
    VoiceEngineInfo(
        id="baidu-asr",
        name="百度智能云 ASR",
        kind="asr",
        description="百度智能云短语音识别标准版 REST API",
    ),
]

TTS_ENGINES = [
    VoiceEngineInfo(
        id="huoshan-tts",
        name="火山引擎 TTS",
        kind="tts",
        description="预留火山引擎语音合成接口",
    ),
    VoiceEngineInfo(
        id="browser-tts",
        name="浏览器语音合成",
        kind="tts",
        description="使用浏览器 SpeechSynthesis 播放",
    ),
    VoiceEngineInfo(
        id="baidu-tts",
        name="百度智能云 TTS",
        kind="tts",
        description="百度智能云短文本在线合成 REST API",
    ),
]


async def get_baidu_access_token(settings: Settings) -> str:
    """Exchange Baidu API Key/Secret Key for an access_token.

    Official Baidu speech APIs use the OAuth client_credentials flow. The token
    response includes expires_in; we refresh one minute early to avoid edge
    cases around expiry.
    """
    if not settings.baidu_api_key or not settings.baidu_secret_key:
        raise RuntimeError("请在 .env 中配置 BAIDU_API_KEY 和 BAIDU_SECRET_KEY")

    now = time.time()
    cached_token = _baidu_token_cache.get("token")
    if cached_token and now < float(_baidu_token_cache.get("expires_at", 0)):
        return str(cached_token)

    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.post(
            "https://aip.baidubce.com/oauth/2.0/token",
            params={
                "grant_type": "client_credentials",
                "client_id": settings.baidu_api_key,
                "client_secret": settings.baidu_secret_key,
            },
            headers={"Accept": "application/json"},
        )
        response.raise_for_status()
        data = response.json()

    if "access_token" not in data:
        raise RuntimeError(f"百度鉴权失败：{data}")

    _baidu_token_cache["token"] = data["access_token"]
    _baidu_token_cache["expires_at"] = now + int(data.get("expires_in", 2592000)) - 60
    return str(data["access_token"])


def detect_audio_format(filename: str | None, content_type: str | None) -> str:
    """Map upload metadata to a Baidu-supported audio format."""
    suffix = Path(filename or "").suffix.lower().lstrip(".")
    if suffix in {"pcm", "wav", "amr", "m4a"}:
        return suffix
    if content_type:
        if "wav" in content_type:
            return "wav"
        if "amr" in content_type:
            return "amr"
        if "mp4" in content_type or "m4a" in content_type:
            return "m4a"
    return "pcm"


async def baidu_transcribe_audio(
    *,
    audio_bytes: bytes,
    filename: str | None,
    content_type: str | None,
    settings: Settings,
) -> str:
    """Call Baidu short speech recognition with JSON/base64 upload."""
    if not audio_bytes:
        raise RuntimeError("百度 ASR 需要上传音频文件")

    token = await get_baidu_access_token(settings)
    audio_format = detect_audio_format(filename, content_type)
    payload = {
        "format": audio_format,
        "rate": settings.baidu_asr_rate,
        "channel": 1,
        "cuid": settings.baidu_cuid,
        "token": token,
        "dev_pid": settings.baidu_asr_dev_pid,
        "speech": base64.b64encode(audio_bytes).decode("ascii"),
        "len": len(audio_bytes),
    }

    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            "https://vop.baidu.com/server_api",
            json=payload,
            headers={"Content-Type": "application/json"},
        )
        response.raise_for_status()
        data = response.json()

    if data.get("err_no") != 0:
        raise RuntimeError(f"百度 ASR 识别失败：{data}")
    return "".join(data.get("result", [])).strip()


async def baidu_synthesize_speech(text: str, settings: Settings) -> str:
    """Call Baidu short text TTS and save the returned MP3 under /media/audio."""
    token = await get_baidu_access_token(settings)
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)

    form = {
        "tex": text,
        "tok": token,
        "cuid": settings.baidu_cuid,
        "ctp": 1,
        "lan": "zh",
        "spd": settings.baidu_tts_spd,
        "pit": settings.baidu_tts_pit,
        "vol": settings.baidu_tts_vol,
        "per": settings.baidu_tts_per,
        "aue": 3,
    }
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            "https://tsn.baidu.com/text2audio",
            data=form,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        response.raise_for_status()

    content_type = response.headers.get("Content-Type", "")
    if "audio" not in content_type and response.content.startswith(b"{"):
        raise RuntimeError(f"百度 TTS 合成失败：{response.text}")

    filename = f"baidu-tts-{int(time.time() * 1000)}.mp3"
    (AUDIO_DIR / filename).write_bytes(response.content)
    return f"/media/audio/{filename}"


async def transcribe_audio(
    engine: str,
    filename: str | None,
    audio_bytes: bytes | None = None,
    content_type: str | None = None,
    settings: Settings | None = None,
) -> str:
    """Transcribe audio with the selected ASR engine."""
    if engine == "baidu-asr":
        if settings is None:
            raise RuntimeError("百度 ASR 缺少配置对象")
        return await baidu_transcribe_audio(
            audio_bytes=audio_bytes or b"",
            filename=filename,
            content_type=content_type,
            settings=settings,
        )
    if engine == "browser-asr":
        return "浏览器已完成语音识别，请在输入框确认文本。"
    suffix = f"（文件：{filename}）" if filename else ""
    return f"语音识别演示结果：今天北京天气怎么样？{suffix}"


async def synthesize_speech(
    engine: str,
    text: str,
    settings: Settings | None = None,
) -> str | None:
    """Synthesize speech with the selected TTS engine.

    Browser TTS returns None because playback happens in the frontend. Cloud TTS
    engines return a URL to an audio file that the frontend can play.
    """
    if engine == "baidu-tts":
        if settings is None:
            raise RuntimeError("百度 TTS 缺少配置对象")
        return await baidu_synthesize_speech(text, settings)
    return None
