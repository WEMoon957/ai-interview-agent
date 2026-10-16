"""《录用通知书》渲染单测。

覆盖：
- 试用期天数 → 中文表述（30→一个月，60→两个月，0→兜底）；
- 通知书包含**固定文案**（公司名、5 项携带材料、法律声明）与
  **HR 填写字段**（工作地点/联系人/薪资/报到时间）；
- 缺字段时返回空串而非报错（存量 Offer 也能正常打开）；
- 日期与截止时间格式化为中文，且**转北京时间**（容器时区是 UTC）。
"""

from datetime import date, datetime, timezone
from types import SimpleNamespace

from app.services.offer_letter import (
    COMPANY_NAME,
    LEGAL_NOTE,
    REPORT_MATERIALS,
    build_letter,
    probation_phrase,
)


def _offer(**kw):
    base = dict(
        candidate_name="李俊颖",
        job_title="AI落地工程师",
        work_location="昆明市西山区万达北塔2507",
        contact_name="方玺",
        contact_phone="17702348593",
        probation_days=60,
        probation_salary="固定8500元",
        regular_salary="10000元/月",
        report_date=date(2026, 9, 27),
        report_time="上午9:00",
        expires_at=datetime(2026, 9, 23, 12, 0, tzinfo=timezone.utc),  # UTC → 北京 20:00
        sent_at=datetime(2026, 9, 23, 2, 0, tzinfo=timezone.utc),
    )
    base.update(kw)
    return SimpleNamespace(**base)


# ── 试用期表述 ──────────────────────────────────────────────
def test_probation_phrase_months():
    assert probation_phrase(30) == "试用期为一个月"
    assert probation_phrase(60) == "试用期为两个月"
    assert probation_phrase(90) == "试用期为三个月"


def test_probation_phrase_edge_cases():
    assert probation_phrase(0) == "试用期"
    assert probation_phrase(None) == "试用期"


# ── 完整渲染 ────────────────────────────────────────────────
def test_letter_contains_fixed_copy_and_filled_fields():
    letter = build_letter(_offer())

    # 固定文案
    assert letter["company_name"] == COMPANY_NAME == "昆明笨二娘餐饮管理有限公司"
    assert letter["title"] == "录用通知书"
    assert letter["materials"] == REPORT_MATERIALS
    assert len(letter["materials"]) == 5
    assert letter["legal_note"] == LEGAL_NOTE

    # HR 填写字段
    assert letter["work_location"] == "昆明市西山区万达北塔2507"
    assert letter["contact_name"] == "方玺"
    assert letter["contact_phone"] == "17702348593"
    assert letter["probation_salary"] == "固定8500元"
    assert letter["regular_salary"] == "10000元/月"
    assert letter["report_time"] == "上午9:00"

    # 系统带出
    assert letter["candidate_name"] == "李俊颖"
    assert letter["job_title"] == "AI落地工程师"
    assert letter["probation_phrase"] == "试用期为两个月"
    assert letter["report_date"] == "2026年9月27日"
    assert letter["letter_date"] == "2026年9月23日"


def test_deadline_converted_to_beijing_time():
    letter = build_letter(_offer())
    # 12:00 UTC = 北京时间 20:00
    assert letter["deadline"] == "2026年9月23日20:00"


def test_letter_tolerates_missing_fields():
    offer = SimpleNamespace(
        candidate_name="张三",
        job_title="",
        report_date=None,
        probation_days=None,
        expires_at=None,
    )
    letter = build_letter(offer)
    assert letter["candidate_name"] == "张三"
    assert letter["job_title"] == ""
    assert letter["report_date"] == ""
    assert letter["deadline"] == ""
    assert letter["work_location"] == ""
    assert letter["probation_phrase"] == "试用期"
    # 固定文案永远在
    assert letter["materials"] == REPORT_MATERIALS
    assert letter["legal_note"] == LEGAL_NOTE
