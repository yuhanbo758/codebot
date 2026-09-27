"""图片与语音服务配置、调用及产物读取。"""
import base64
import os
import secrets
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from typing import Literal, Optional

from config import MediaConfig, MediaServiceConfig, app_config, save_config
from core.media_runtime import MediaError, asset_path, credential_configured, generate, set_desktop_key, supported_protocols, validate_service


router = APIRouter()


class MediaRequest(BaseModel):
    kind: Literal["image", "speech", "transcription"]
    prompt: str = Field(default="", max_length=10000)
    audio_base64: str = Field(default="", max_length=28_000_000)
    filename: str = Field(default="audio.mp3", max_length=255)


class MediaDesktopKeyRequest(BaseModel):
    kind: Literal["image", "speech", "transcription"]
    protocol: str = Field(default="", max_length=50)
    api_key: str = Field(default="", max_length=4096)


class MediaConfigUpdate(BaseModel):
    image: Optional[MediaServiceConfig] = None
    speech: Optional[MediaServiceConfig] = None
    transcription: Optional[MediaServiceConfig] = None


@router.get("/config")
async def get_media_config():
    return {"success": True, "data": {**app_config.media.model_dump(),
            "credentials": {kind: credential_configured(kind) for kind in ("image", "speech", "transcription")},
            "credential_protocols": {kind: {protocol: credential_configured(kind, protocol)
                for protocol in supported_protocols(kind)} for kind in ("image", "speech", "transcription")}}}


@router.patch("/config")
async def update_media_config(body: MediaConfigUpdate):
    values = {**app_config.media.model_dump(), **body.model_dump(exclude_unset=True)}
    updated = MediaConfig(**values)
    try:
        for kind in ("image", "speech", "transcription"):
            validate_service(kind, getattr(updated, kind))
    except MediaError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    app_config.media = updated
    save_config(app_config)
    return await get_media_config()


@router.post("/desktop-key", include_in_schema=False)
async def update_media_desktop_key(request: Request, body: MediaDesktopKeyRequest):
    expected = os.environ.get("CODEBOT_DESKTOP_BRIDGE_TOKEN", "")
    supplied = request.headers.get("x-codebot-desktop-token", "")
    if request.client is None or request.client.host not in {"127.0.0.1", "::1"} or not expected or not secrets.compare_digest(expected, supplied):
        raise HTTPException(status_code=404, detail="Not found")
    try:
        set_desktop_key(body.kind, body.api_key, body.protocol)
    except MediaError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"success": True, "data": {"configured": bool(body.api_key)}}


@router.post("/generate")
async def generate_media(body: MediaRequest):
    try:
        audio = base64.b64decode(body.audio_base64, validate=True) if body.audio_base64 else None
        result = await generate(body.kind, body.prompt, audio=audio, filename=body.filename)
        return {"success": True, "data": result}
    except (ValueError, base64.binascii.Error, MediaError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/assets/{name}")
async def get_media_asset(name: str):
    try:
        path = asset_path(name)
    except MediaError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return FileResponse(path, filename=Path(name).name, content_disposition_type="inline",
                        headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "private, max-age=3600"})
