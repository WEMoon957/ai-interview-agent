"""《录用通知书》的固定文案与数据组装。

候选人打开 ``/o/{token}`` 时看到的是一份正式《录用通知书》，内容分三类：

- **固定文案**（全公司统一，写死在本模块）：公司名、报到携带资料、法律声明、
  「可视情况提前转正」这类说明；
- **HR 填写**（每个候选人可能不同）：工作地点、联系人、联系电话、
  试用期薪资、转正薪资、报到具体时间；
- **系统带出**：姓名、岗位、报到日期、确认截止时间、落款日期。

后端直接把文案拼好给前端，前端只负责排版，避免同一句文案散落在前后端两处。
"""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

# 全公司统一的固定文案
COMPANY_NAME = "昆明笨二娘餐饮管理有限公司"

# 报到需携带的材料（顺序与线下模板一致）
REPORT_MATERIALS: list[str] = [
    "身份证原件",
    "毕业证原件",
    "本人中国银行银行卡",
    "原单位离职证明",
    "近三个月内体检报告一份（需包含常规内外科检查、肝功能、血常规、尿常规、胸部X光、甲功三项等）",
]

# 法律声明
LEGAL_NOTE = "本通知为录用确认，具体权利义务以正式劳动合同为准。"

# 试用期说明的后半句
PROBATION_SUFFIX = "，可视情况提前转正"

# 服务器容器时区是 UTC，展示给候选人必须显式转北京时间，否则会差 8 小时
CN_TZ = ZoneInfo("Asia/Shanghai")

# 中文月份习惯：2 读作「两」（写「两个月」而非「二个月」）
_ZH_NUM = {
    1: "一", 2: "两", 3: "三", 4: "四", 5: "五", 6: "六",
    7: "七", 8: "八", 9: "九", 10: "十", 11: "十一", 12: "十二",
}


def probation_phrase(days: int | None) -> str:
    """试用期天数 → 中文表述：30 → 「试用期为一个月」，60 → 「试用期为两个月」。"""
    d = days or 0
    if d <= 0:
        return "试用期"
    months = max(1, round(d / 30))
    return f"试用期为{_ZH_NUM.get(months, str(months))}个月"


def _fmt_date(value) -> str:
    """date → 「2026年9月27日」。"""
    if isinstance(value, date):
        return f"{value.year}年{value.month}月{value.day}日"
    return ""


def _fmt_datetime_cn(value) -> str:
    """datetime → 「2026年9月23日20:00」（转北京时间）。"""
    if not isinstance(value, datetime):
        return ""
    try:
        local = value.astimezone(CN_TZ)
    except (ValueError, OSError):
        local = value
    return f"{local.year}年{local.month}月{local.day}日{local.hour}:{local.minute:02d}"


def build_letter(offer, *, now: datetime | None = None) -> dict:
    """组装候选人确认页所需的通知书数据（固定文案 + HR 填写 + 系统带出）。

    缺字段时返回空串，前端按空值省略对应行，保证存量 Offer 也能正常打开。
    """
    stamp = (
        getattr(offer, "sent_at", None)
        or getattr(offer, "approved_at", None)
        or now
        or datetime.now(tz=CN_TZ)
    )
    if isinstance(stamp, datetime) and stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=CN_TZ)

    return {
        "company_name": COMPANY_NAME,
        "title": "录用通知书",
        "candidate_name": getattr(offer, "candidate_name", "") or "",
        "job_title": getattr(offer, "job_title", "") or "",
        "work_location": getattr(offer, "work_location", "") or "",
        "contact_name": getattr(offer, "contact_name", "") or "",
        "contact_phone": getattr(offer, "contact_phone", "") or "",
        "probation_phrase": probation_phrase(getattr(offer, "probation_days", None)),
        "probation_suffix": PROBATION_SUFFIX,
        "probation_salary": getattr(offer, "probation_salary", "") or "",
        "regular_salary": getattr(offer, "regular_salary", "") or "",
        "report_date": _fmt_date(getattr(offer, "report_date", None)),
        "report_time": getattr(offer, "report_time", "") or "",
        "materials": list(REPORT_MATERIALS),
        "legal_note": LEGAL_NOTE,
        "deadline": _fmt_datetime_cn(getattr(offer, "expires_at", None)),
        "letter_date": _fmt_date(stamp),
    }
