"""面试录音存储单测：格式白名单、流式落盘、大小上限、路径穿越防护。"""
import asyncio
import io
from pathlib import Path

import pytest
from starlette.datastructures import UploadFile as StarletteUploadFile

import app.services.audio_storage as store
from app.config import Settings
from app.exceptions import BizError


def make_settings(tmp_path, **overrides) -> Settings:
    """把上传根目录指向临时目录，隔离真实磁盘。"""
    base = dict(
        upload_dir=str(tmp_path),
        max_audio_mb=1,
    )
    base.update(overrides)
    return Settings(_env_file=None, **base)


def _upload(filename: str, data: bytes) -> StarletteUploadFile:
    return StarletteUploadFile(filename=filename, file=io.BytesIO(data))


# ────────────────────────────── 格式白名单 ──────────────────────────────


@pytest.mark.parametrize(
    "name,ok",
    [
        ("面试录音.m4a", True),
        ("rec.MP3", True),  # 大小写不敏感
        ("a.mp4", True),
        ("b.wav", True),
        ("c.amr", True),
        ("d.3gp", True),
        ("x.pdf", False),
        ("x.txt", False),
        ("noext", False),
        ("", False),
    ],
)
def test_is_allowed_audio(name, ok):
    assert store.is_allowed_audio(name) is ok


# ────────────────────────────── 落盘 ──────────────────────────────


def test_save_audio_writes_file_with_uuid_name(tmp_path):
    settings = make_settings(tmp_path)
    result = asyncio.run(store.save_audio_upload(_upload("面试录音.m4a", b"\x01" * 2048), settings=settings))

    assert result["name"] == "面试录音.m4a"  # 原始文件名保留在字段里
    assert result["size"] == 2048
    assert result["rel_path"].startswith("audio/")
    assert Path(result["rel_path"]).suffix == ".m4a"

    abs_path = tmp_path / result["rel_path"]
    assert abs_path.exists()
    # 磁盘名应是 uuid，不含原始文件名（避免中文/特殊字符与路径穿越）
    assert "面试录音" not in abs_path.name


def test_save_audio_rejects_unknown_extension(tmp_path):
    with pytest.raises(BizError) as exc:
        asyncio.run(store.save_audio_upload(_upload("简历.pdf", b"x"), settings=make_settings(tmp_path)))
    assert exc.value.http_status == 422
    assert "不支持的音频格式" in exc.value.message


def test_save_audio_rejects_empty_file(tmp_path):
    with pytest.raises(BizError) as exc:
        asyncio.run(store.save_audio_upload(_upload("空.m4a", b""), settings=make_settings(tmp_path)))
    assert exc.value.http_status == 422
    assert "为空" in exc.value.message


def test_save_audio_rejects_oversize_and_cleans_up(tmp_path):
    """超过上限要报 413，且不留半截文件。"""
    settings = make_settings(tmp_path, max_audio_mb=1)
    with pytest.raises(BizError) as exc:
        asyncio.run(
            store.save_audio_upload(_upload("大文件.m4a", b"\x00" * (1024 * 1024 + 10)), settings=settings)
        )
    assert exc.value.http_status == 413
    assert "上限" in exc.value.message
    # 目录里不应残留任何文件
    leftover = list((tmp_path / "audio").rglob("*")) if (tmp_path / "audio").exists() else []
    assert [p for p in leftover if p.is_file()] == []


# ────────────────────────────── 路径解析与清理 ──────────────────────────────


def test_audio_abs_path_resolves_under_root(tmp_path):
    settings = make_settings(tmp_path)
    assert store.audio_abs_path("audio/2026/10/x.m4a", settings=settings) == (
        tmp_path / "audio/2026/10/x.m4a"
    ).resolve()


@pytest.mark.parametrize("bad", ["../outside.m4a", "audio/../../escape.m4a", "/etc/passwd"])
def test_audio_abs_path_blocks_traversal(tmp_path, bad):
    with pytest.raises(BizError) as exc:
        store.audio_abs_path(bad, settings=make_settings(tmp_path))
    assert exc.value.http_status == 400


def test_delete_audio_is_best_effort(tmp_path):
    settings = make_settings(tmp_path)
    store.delete_audio(None, settings=settings)  # 空路径不报错
    store.delete_audio("audio/不存在.m4a", settings=settings)  # 不存在也不报错

    saved = asyncio.run(store.save_audio_upload(_upload("a.m4a", b"123"), settings=settings))
    target = tmp_path / saved["rel_path"]
    assert target.exists()
    store.delete_audio(saved["rel_path"], settings=settings)
    assert not target.exists()


def test_max_audio_bytes_derives_from_mb(tmp_path):
    assert make_settings(tmp_path, max_audio_mb=50).max_audio_bytes == 50 * 1024 * 1024
    # 非法值兜底为 1MB，不返回 0 或负数
    assert make_settings(tmp_path, max_audio_mb=0).max_audio_bytes == 1024 * 1024
