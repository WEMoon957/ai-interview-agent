"""密码安全原语：Argon2id 哈希 + 强度校验 + 常见弱口令比对。

安全约定（不可绕过）：

1. 只允许用 **Argon2id** 存储密码哈希。禁止明文、MD5、SHA1/SHA256 裸哈希、
   或任何自研哈希算法（见项目安全基线）。
2. 参数取 OWASP《Password Storage Cheat Sheet》推荐档位之上的配置
   （64 MiB 内存 / 3 次迭代 / 4 路并行），兼顾抗 GPU 爆破与登录延迟。
3. 密码比对一律走 ``PasswordHasher.verify``，算法内部为恒定时间比较，
   不要在业务代码里手写 ``==`` 比较哈希。

本模块不依赖数据库或 FastAPI，可独立单测。
"""
from __future__ import annotations

import re
import unicodedata

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from argon2.low_level import Type

# ── Argon2id 参数（模块级单例，复用内部缓存避免重复分配）─────────────
_hasher = PasswordHasher(
    time_cost=3,
    memory_cost=64 * 1024,  # 64 MiB
    parallelism=4,
    hash_len=32,
    salt_len=16,
    type=Type.ID,  # Argon2id：兼顾抗侧信道（i）与抗 GPU（d）
)

# 强度门槛
MIN_PASSWORD_LENGTH = 10
# 上限用于避免超长输入造成的解析开销（同时挡住"用 1MB 密码打登录接口"的低成本滥用）
MAX_PASSWORD_LENGTH = 128
# 至少需要命中的字符类别数（大写/小写/数字/符号 四选三）
MIN_CHAR_CLASSES = 3

# ── 常见弱口令表 ────────────────────────────────────────────────────
# 写入的是"归一化后的规范形式"（小写、无空格）。比对前会把用户输入做
# 变形展开（leet 还原、去尾缀数字等），因此这里无需罗列所有变体。
_COMMON_PASSWORDS: frozenset[str] = frozenset(
    {
        # 数字流水
        "123456", "1234567", "12345678", "123456789", "1234567890", "12345678910",
        "111111", "1111111", "11111111", "000000", "00000000", "222222", "333333",
        "444444", "555555", "666666", "777777", "888888", "88888888", "999999",
        "123123", "123123123", "112233", "121212", "123321", "654321", "0987654321",
        "102030", "5201314", "520520", "1314520", "100200", "159357", "147258369",
        # 键盘路径
        "qwerty", "qwertyuiop", "qwerty123", "qwer1234", "qazwsx", "qazwsxedc",
        "1qaz2wsx", "1qazxsw2", "1q2w3e4r", "1q2w3e4r5t", "q1w2e3r4", "zxcvbnm",
        "asdfgh", "asdfghjkl", "asdf1234", "zxcasdqwe", "123qwe", "qwe123",
        "abc123", "abcd1234", "a123456", "a1b2c3", "abcabc", "aabbcc",
        # 英文弱口令
        "password", "password1", "passw0rd", "passwd", "pass", "letmein",
        "welcome", "monkey", "dragon", "master", "shadow", "sunshine",
        "iloveyou", "princess", "football", "baseball", "superman", "batman",
        "trustno1", "whatever", "starwars", "computer", "internet", "samsung",
        "google", "secret", "freedom", "changeme", "login", "administrator",
        "temp", "temp123", "test", "test123", "tester", "guest", "guest123",
        "user", "user123", "demo", "demo123", "sample", "default", "system",
        # 管理/运维弱口令
        "admin", "admin123", "admin888", "adminadmin", "administrator123",
        "root", "root123", "toor", "manager", "manager123", "sysadmin",
        "operator", "supervisor", "hr", "hr123456", "boss", "boss123",
        # 中文拼音弱口令
        "woaini", "woaini1314", "woaini520", "woaini123", "wangyi", "lilei",
        "xiaoming", "zhangwei", "wangwei", "liwei", "zhangsan", "lisi",
        "wangwu", "zhaoliu", "buzhidao", "meiyou", "shuaige", "meinv",
        "nihao", "nihaoma", "xiexie", "zaijian", "zhongguo", "beijing",
        # 本系统针对性弱口令：品牌、业务、角色词
        "benerniang", "benerniang123", "benerniang888", "benerniang2026",
        "longjiang", "longjiang123", "zhujiaofan", "zhujiao", "interview",
        "interview123", "recruit", "recruit123", "zhaopin", "zhaopin123",
        "mianshi", "mianshi123", "offer", "offer123", "hr2026", "admin2026",
    }
)

# 符号型 leet 变形还原表。
# 只映射"符号冒充字母"的情况（@→a、$→s、!→i、0→o）：
# 绝不映射 1/3/4/5/7/8 —— 那些位置在真实密码里更常是数字本身，
# 强行还原会破坏 "password123" 这类尾缀数字的剥离。
_SYMBOL_LEET = str.maketrans(
    {
        "@": "a",
        "$": "s",
        "!": "i",
        "0": "o",
    }
)


def hash_password(plain: str) -> str:
    """把明文密码哈希为 Argon2id 字符串（含算法参数与随机盐，可直接入库）。

    Args:
        plain: 明文密码。

    Returns:
        ``$argon2id$v=19$m=65536,t=3,p=4$<salt>$<hash>`` 形式的编码串。
    """
    return _hasher.hash(plain)


def verify_password(stored_hash: str, plain: str) -> bool:
    """校验明文密码是否与库中哈希匹配。

    使用算法自带的恒定时间比较；哈希格式非法或校验失败统一返回 False，
    不向调用方区分失败原因（避免泄露"这个账号的哈希是坏的"这类信息）。
    """
    try:
        return _hasher.verify(stored_hash, plain)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False
    except Exception:  # noqa: BLE001 - 任何解析异常都视为校验失败，绝不抛给上层
        return False


def needs_rehash(stored_hash: str) -> bool:
    """判断库中哈希是否与当前参数档位不一致（升级参数后用于惰性重哈希）。"""
    try:
        return _hasher.check_needs_rehash(stored_hash)
    except (InvalidHashError, VerificationError):
        return True


def _variants(value: str) -> set[str]:
    """把密码展开为若干归一化候选串，用于弱口令比对。

    在"候选串"层面做变形展开（而非对输入做单次归一化），是因为不同变形
    要各自参与比对：``Benerniang@123`` 需要先剥尾缀数字再剥符号才能命中
    ``benerniang``，而 ``woaini1314`` 本身就带数字、必须原样命中。

    生成的候选包括：原串、去空白、符号 leet 还原，以及各自的
    "剥尾缀数字/符号""剥前导数字/符号"变体。
    """
    base = unicodedata.normalize("NFKC", value or "").strip().lower()
    squeezed = re.sub(r"\s+", "", base)
    leet = squeezed.translate(_SYMBOL_LEET)

    candidates: set[str] = {base, squeezed, leet}
    for candidate in (squeezed, leet):
        # 剥尾缀：password123 → password → admin@ → admin
        tail = re.sub(r"[\W_]+$", "", re.sub(r"\d+$", "", candidate))
        # 剥前缀：123abc → abc
        head = re.sub(r"^[\W_\d]+", "", candidate)
        candidates.add(tail)
        candidates.add(head)

    return {item for item in candidates if item}


def is_common_password(password: str) -> bool:
    """密码是否命中常见弱口令表（含常见变形）。"""
    return bool(_variants(password) & _COMMON_PASSWORDS)


def password_problems(
    password: str,
    *,
    username: str | None = None,
    email: str | None = None,
) -> list[str]:
    """校验密码强度，返回全部不合规原因的清单（空列表 = 通过）。

    拆成"返回原因"而不是直接抛异常，是为了让调用方决定响应形态，
    同时方便单元测试逐条断言。

    Args:
        password: 待校验的明文密码。
        username: 关联用户名（用于"密码不得包含用户名"检查）。
        email: 关联邮箱（同样禁止用邮箱前缀做密码）。
    """
    problems: list[str] = []

    if not password:
        return ["密码不能为空"]

    if len(password) < MIN_PASSWORD_LENGTH:
        problems.append(f"密码长度至少 {MIN_PASSWORD_LENGTH} 位")
    if len(password) > MAX_PASSWORD_LENGTH:
        problems.append(f"密码长度不能超过 {MAX_PASSWORD_LENGTH} 位")

    classes = [
        bool(re.search(r"[a-z]", password)),
        bool(re.search(r"[A-Z]", password)),
        bool(re.search(r"\d", password)),
        bool(re.search(r"[^A-Za-z0-9]", password)),
    ]
    if sum(classes) < MIN_CHAR_CLASSES:
        problems.append(
            f"密码需包含大写字母、小写字母、数字、特殊字符中的至少 {MIN_CHAR_CLASSES} 类"
        )

    if is_common_password(password):
        problems.append("该密码属于常见弱口令，请更换")

    # 与账号信息高度相关：攻击者拿到用户名后第一时间就会试这些组合
    lowered = (password or "").lower()
    for label, value in (("用户名", username), ("邮箱", email)):
        if not value:
            continue
        token = value.strip().lower()
        if "@" in token and label == "邮箱":
            token = token.split("@", 1)[0]
        if len(token) >= 3 and token in lowered:
            problems.append(f"密码不能包含{label}信息")

    # 纯重复或纯连续（如 aaaaaaaa、abcdabcd）——长度够了但熵极低
    if len(password) >= MIN_PASSWORD_LENGTH and len(set(password)) <= 2:
        problems.append("密码字符过于单一，请增加字符变化")

    return problems


def ensure_strong_password(
    password: str,
    *,
    username: str | None = None,
    email: str | None = None,
) -> None:
    """密码不合规时抛出 422 业务异常（供接口层直接调用）。"""
    from app.exceptions import BizError

    problems = password_problems(password, username=username, email=email)
    if problems:
        raise BizError(422, "；".join(problems), http_status=422)
