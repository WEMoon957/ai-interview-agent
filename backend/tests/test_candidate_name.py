"""候选人姓名提取：正文优先、文件名兜底。"""
from app.services.resume_parser import (
    extract_candidate_name,
    extract_name_from_filename,
    extract_name_from_text,
)


def test_name_from_text_label():
    assert extract_name_from_text("姓名：张三\n电话：13800138000") == "张三"
    assert extract_name_from_text("姓 名：李四") == "李四"
    assert extract_name_from_text("姓名：欧阳娜娜 性别：女") == "欧阳娜娜"
    assert extract_name_from_text("姓名：张三，电话：13800138000") == "张三"
    assert extract_name_from_text("Name: John Smith\nPhone: 123") == "John Smith"


def test_name_from_text_first_line_fallback():
    assert extract_name_from_text("王小明\n电话：13800138000") == "王小明"
    assert extract_name_from_text("个人简历\n工作经历：某餐厅后厨") is None
    assert extract_name_from_text("Br Br x x M M U 20c6a229") is None


def test_name_from_filename():
    # 生产实际错例：旧逻辑在 4-8K 的 '-' 处截断
    assert extract_name_from_filename("【招聘专员_昆明 4-8K】李女士 7年.pdf") == "李女士"
    assert extract_name_from_filename("张三-后厨帮厨.txt") == "张三"
    assert extract_name_from_filename("简历-李四.pdf") == "李四"
    assert extract_name_from_filename("王五_简历_2024.docx") == "王五"
    assert extract_name_from_filename("resume.pdf") == ""


def test_combined_name_prefers_text_then_filename():
    assert extract_candidate_name("姓名：张三", "【岗位】李女士.pdf") == "张三"
    assert extract_candidate_name("乱码", "【岗位】李女士.pdf") == "李女士"
    assert extract_candidate_name("", "resume.pdf") == "未命名候选人"
