"""面试录音的落盘存储。

设计要点
--------
- **本地磁盘，零新基础设施**：文件写到 ``settings.upload_root``（默认 backend/uploads）下的
  ``audio/YYYY/MM/<uuid>.<ext>``。容器里 api 的工作目录是 ``/app`` 且 compose 绑定了
  ``./backend:/app``，因此文件天然持久化到宿主机备份目录，无需对象存储。
- **流式写入 + 大小上限**：分块读分块写，超限立刻中止并删掉半截文件，
  避免店长上传大文件时把内存/磁盘打满。
- **只存相对路径**：库里存相对路径，迁移/换盘时不用改数据。
- **对外不暴露真实文件名**：磁盘名是 uuid，原始文件名单独存字段，下载时再还原。
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import UploadFile

from app.config import Settings, get_settings
from app.exceptions import BizError

logger = logging.getLogger(__name__)

# 允许的音频/录音容器格式（手机录音常见 m4a / mp4 / 3gp / amr，一并放开）
ALLOWED_AUDIO_EXT = {
    ".mp3",
    ".m4a",
    ".mp4",
    ".wav",
    ".aac",
    ".ogg",
    ".oga",
    ".opus",
    ".flac",
    ".amr",
    ".3gp",
    ".mov",
    ".webm",
    ".caf",
    ".wma",
}
ALLOWED_AUDIO_HINT = "mp3 / m4a / mp4 / wav / aac / ogg / opus / flac / amr / 3gp / mov / webm"

_CHUNK = 1024 * 1024  # 1MB 分块


def is_allowed_audio(filename: str) -> bool:
    """扩展名是否在白名单内。"""
    return Path(filename or "").suffix.lower() in ALLOWED_AUDIO_EXT


async def save_audio_upload(
    upload: UploadFile,
    *,
    settings: Settings | None = None,
) -> dict:
    """把上传的录音流式落盘。

    Returns:
        ``{"rel_path": "audio/2026/10/xxx.m4a", "name": 原始文件名, "size": 字节数}``

    Raises:
        BizError: 格式不支持(422) / 空文件(422) / 超出上限(413)
    """
    settings = settings or get_settings()
    original = upload.filename or "recording"
    if not is_allowed_audio(original):
        raise BizError(
            422,
            f"不支持的音频格式（支持 {ALLOWED_AUDIO_HINT}）",
            http_status=422,
        )

    ext = Path(original).suffix.lower()
    now = datetime.now(timezone.utc)
    rel_dir = Path("audio") / now.strftime("%Y") / now.strftime("%m")
    abs_dir = settings.upload_root / rel_dir
    abs_dir.mkdir(parents=True, exist_ok=True)

    abs_path = abs_dir / f"{uuid.uuid4().hex}{ext}"
    limit = settings.max_audio_bytes
    size = 0
    try:
        with abs_path.open("wb") as fh:
            while True:
                chunk = await upload.read(_CHUNK)
                if not chunk:
                    break
                size += len(chunk)
                if size > limit:
                    raise BizError(
                        413,
                        f"录音超过 {settings.max_audio_mb}MB 上限，请压缩后再传",
                        http_status=413,
                    )
                fh.write(chunk)
    except BizError:
        abs_path.unlink(missing_ok=True)
        raise
    except OSError as exc:
        abs_path.unlink(missing_ok=True)
        logger.exception("录音落盘失败：%s", exc)
        raise BizError(500, f"录音保存失败：{exc}", http_status=500)

    if size == 0:
        abs_path.unlink(missing_ok=True)
        raise BizError(422, "录音文件为空", http_status=422)

    return {"rel_path": str(rel_dir / abs_path.name), "name": original, "size": size}


def audio_abs_path(rel_path: str, *, settings: Settings | None = None) -> Path:
    """把库里的相对路径还原为绝对路径（并做路径穿越防护）。"""
    settings = settings or get_settings()
    root = settings.upload_root
    target = (root / rel_path).resolve()
    if not str(target).startswith(str(root)):
        raise BizError(400, "非法的文件路径", http_status=400)
    return target


def delete_audio(rel_path: str | None, *, settings: Settings | None = None) -> None:
    """删除录音文件（尽力而为，文件不存在不报错）。"""
    if not rel_path:
        return
    try:
        audio_abs_path(rel_path, settings=settings).unlink(missing_ok=True)
    except Exception:  # noqa: BLE001 - 清理失败不影响主流程
        logger.warning("删除录音失败（已忽略）：%s", rel_path)
