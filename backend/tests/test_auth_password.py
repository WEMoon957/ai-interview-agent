"""密码安全模块单测：Argon2id 哈希、强度校验、弱口令比对。

覆盖安全基线要求的正向与反向用例：
- 哈希必须是 Argon2id 且两次哈希不同（随机盐）；
- 明文/MD5/SHA1 形式的"哈希"绝不能被误判为合法；
- 常见弱口令（含 leet 变形、品牌定制弱口令）必须被拒绝。
"""
import hashlib

import pytest

from app.security.passwords import (
    MIN_PASSWORD_LENGTH,
    ensure_strong_password,
    hash_password,
    is_common_password,
    needs_rehash,
    password_problems,
    verify_password,
)


# ── 哈希与校验 ────────────────────────────────────────────────
def test_hash_is_argon2id_and_verifies():
    """正向：Argon2id 哈希可被正确校验。"""
    stored = hash_password("Str0ng-Passw0rd!")
    assert stored.startswith("$argon2id$")
    assert verify_password(stored, "Str0ng-Passw0rd!") is True


def test_wrong_password_rejected():
    """反向：错误密码必须校验失败。"""
    stored = hash_password("Str0ng-Passw0rd!")
    assert verify_password(stored, "Str0ng-Passw0rd") is False
    assert verify_password(stored, "str0ng-passw0rd!") is False
    assert verify_password(stored, "") is False


def test_same_password_yields_different_hash():
    """每次哈希使用随机盐，相同密码不应产生相同摘要。"""
    assert hash_password("Str0ng-Passw0rd!") != hash_password("Str0ng-Passw0rd!")


def test_plaintext_and_weak_digests_never_validate():
    """明文 / MD5 / SHA1 形式绝不能通过校验（防降级为不安全存储）。"""
    plain = "Str0ng-Passw0rd!"
    for bogus in (
        plain,
        hashlib.md5(plain.encode()).hexdigest(),
        hashlib.sha1(plain.encode()).hexdigest(),
        "",
        "not-a-hash",
        "$2b$12$abcdefghijklmnopqrstuv",
    ):
        assert verify_password(bogus, plain) is False


def test_verify_never_raises_on_garbage_input():
    """畸形哈希不得抛异常（避免 500 与栈信息外泄）。"""
    assert verify_password("", "") is False
    assert verify_password("$argon2id$broken", "x") is False


def test_needs_rehash_detects_legacy_and_broken_hashes():
    """参数一致时不需重哈希；非法哈希视为需要重哈希。"""
    assert needs_rehash(hash_password("Str0ng-Passw0rd!")) is False
    assert needs_rehash("whatever") is True


# ── 弱口令比对 ────────────────────────────────────────────────
@pytest.mark.parametrize(
    "weak",
    [
        "123456",
        "password",
        "qwerty123",
        "1qaz2wsx",
        "woaini1314",
        "Admin@123",  # leet/大小写变形后命中 admin123
        "Benerniang@123",  # 品牌定制弱口令 + 尾缀数字
        "benerniang2026",
        "zhaopin123",
        "Password123",  # 尾缀数字剥离后命中 password
    ],
)
def test_common_passwords_rejected(weak):
    """常见弱口令及其变形必须命中弱口令表。"""
    assert is_common_password(weak) is True
    assert any("弱口令" in p for p in password_problems(weak))


def test_strong_password_not_flagged_as_common():
    """强密码不应被误判为弱口令（避免误伤）。"""
    assert is_common_password("Kf7#mQ2vLp9x") is False
    assert is_common_password("Tr0ub4dor&3xQz") is False


# ── 强度校验 ──────────────────────────────────────────────────
def test_short_password_rejected():
    """长度不足必须被拒绝。"""
    problems = password_problems("Ab3$")
    assert any("长度至少" in p for p in problems)
    assert len("Ab3$") < MIN_PASSWORD_LENGTH


def test_insufficient_char_classes_rejected():
    """只含单一或少数字符类别必须被拒绝。"""
    problems = password_problems("abcdefghijkl")  # 仅小写
    assert any("至少" in p and "类" in p for p in problems)

    problems = password_problems("abcdefghij1")  # 小写 + 数字，仅两类
    assert any("至少" in p and "类" in p for p in problems)


def test_password_containing_username_rejected():
    """密码包含用户名/邮箱前缀必须被拒绝。"""
    problems = password_problems("Lijunying#2026", username="lijunying")
    assert any("用户名" in p for p in problems)

    problems = password_problems("Hr#LiJunying2026", email="lijunying@example.com")
    assert any("邮箱" in p for p in problems)


def test_low_entropy_password_rejected():
    """超长但字符集极小的密码（aaaaaaaaaa）必须被拒绝。"""
    problems = password_problems("aaaaaaaaaaaa")
    assert any("单一" in p for p in problems)


def test_strong_password_passes():
    """强密码应无任何问题。"""
    assert password_problems("Kf7#mQ2vLp9x") == []
    assert password_problems("Tr0ub4dor&3xQz", username="hr") == []


def test_ensure_strong_password_raises_biz_error():
    """ensure_* 包装层应抛 422 业务异常并带上原因。"""
    from app.exceptions import BizError

    with pytest.raises(BizError) as exc:
        ensure_strong_password("123456")
    assert exc.value.http_status == 422
    assert "弱口令" in exc.value.message

    # 合规密码不抛异常
    ensure_strong_password("Kf7#mQ2vLp9x")
