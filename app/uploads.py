from __future__ import annotations

"""Upload helpers for multimodal chat.

Uploaded images are stored under data/uploads and exposed through
/media/uploads. Providers read those files back and encode them as data URLs
when calling vision-capable OpenAI-compatible APIs.
"""

import base64
import imghdr
from pathlib import Path
from uuid import uuid4

from fastapi import UploadFile

from app.schemas import AttachmentInfo

BASE_DIR = Path(__file__).resolve().parent.parent
UPLOAD_DIR = BASE_DIR / "data" / "uploads"
MAX_UPLOAD_BYTES = 8 * 1024 * 1024
ALLOWED_IMAGE_TYPES = {"image/png", "image/jpeg", "image/webp", "image/gif"}


def upload_path(attachment_id: str) -> Path:
    """Resolve an attachment id to a path inside the upload directory."""
    safe_name = Path(attachment_id).name
    path = (UPLOAD_DIR / safe_name).resolve()
    if UPLOAD_DIR.resolve() not in path.parents:
        raise ValueError("Invalid attachment path")
    return path


async def save_upload(file: UploadFile) -> AttachmentInfo:
    """Validate and save one uploaded image."""
    content = await file.read()
    if not content:
        raise ValueError("上传文件为空")
    if len(content) > MAX_UPLOAD_BYTES:
        raise ValueError("图片不能超过 8MB")

    content_type = file.content_type or ""
    if content_type not in ALLOWED_IMAGE_TYPES:
        raise ValueError("仅支持 PNG、JPEG、WebP、GIF 图片")

    # imghdr is a small extra guard against files with fake Content-Type.
    image_kind = imghdr.what(None, h=content)
    if image_kind not in {"png", "jpeg", "webp", "gif"}:
        raise ValueError("文件内容不是有效图片")

    suffix = {
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/webp": ".webp",
        "image/gif": ".gif",
    }[content_type]
    attachment_id = f"{uuid4().hex}{suffix}"
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    path = upload_path(attachment_id)
    path.write_bytes(content)

    return AttachmentInfo(
        id=attachment_id,
        filename=file.filename or attachment_id,
        content_type=content_type,
        url=f"/media/uploads/{attachment_id}",
        size=len(content),
    )


def attachment_to_data_url(attachment: AttachmentInfo) -> str:
    """Read an uploaded image and return a data URL for vision APIs."""
    path = upload_path(attachment.id)
    content = path.read_bytes()
    encoded = base64.b64encode(content).decode("ascii")
    return f"data:{attachment.content_type};base64,{encoded}"


def describe_attachments(attachments: list[AttachmentInfo]) -> str:
    """Create a compact human-readable attachment summary for demo responses."""
    if not attachments:
        return "未上传图片。"
    return "\n".join(
        f"- {item.filename}（{item.content_type}，{round(item.size / 1024, 1)} KB）"
        for item in attachments
    )
