"""简历解析：PDF / DOCX / 纯文本；并从正文/文件名提取候选人姓名。"""
import io
import re

# 姓名标签：姓名：张三 / 姓 名 张三 / Name: John Smith
_NAME_LABEL_RE = re.compile(
    r"(?:姓\s*名|名字|Name)\s*[:：]?\s*"
    r"([\u4e00-\u9fa5·]{2,8}|[A-Za-z][A-Za-z .'\-]{1,40})"
    r"(?=\s|$|[,，。;；]|性别|年龄|电话|手机|邮箱|民族|籍贯|地址|现居|求职|工作|教育|政治|婚姻)",
    re.IGNORECASE,
)

# 不是姓名的常见栏目/字段词
_NAME_STOPWORDS = {
    "姓名", "名字", "个人简历", "求职简历", "应聘登记表", "简历", "个人信息",
    "基本信息", "求职意向", "工作经历", "教育背景", "教育经历", "项目经历",
    "自我评价", "联系方式", "电话", "邮箱", "性别", "年龄", "民族", "籍贯",
    "地址", "现居", "期望薪资", "到岗时间", "政治面貌", "婚姻状况",
    "resume", "cv", "file", "document", "download",
}

# 文件名里常见的岗位/地点/修饰词，避免把岗位或地点当姓名
_FILENAME_NOISE = {
    "招聘", "专员", "经理", "主管", "助理", "文员", "客服", "销售", "前台",
    "后厨", "帮厨", "服务员", "收银", "店员", "厨师", "店长", "保洁", "保安",
    "配送", "司机", "会计", "出纳", "人事", "行政", "运营", "设计", "开发",
    "工程师", "简历", "个人简历", "应聘", "求职", "副本", "最终", "版",
    "昆明", "北京", "上海", "广州", "深圳", "成都", "杭州", "武汉", "西安",
    "重庆", "南京", "长沙", "郑州", "青岛", "厦门", "福州", "合肥", "济南",
    "大连", "沈阳", "哈尔滨", "长春", "石家庄", "太原", "南昌", "贵阳",
    "南宁", "海口", "兰州", "银川", "西宁", "乌鲁木齐", "呼和浩特", "拉萨",
    "天津", "苏州", "无锡", "宁波", "东莞", "佛山", "珠海", "中山", "惠州",
}


def _valid_name(value: str) -> str | None:
    """校验一个候选字符串是否像姓名；不像则返回 None。"""
    s = (value or "").strip().strip("：:，,。.、;；")
    if not s or s.lower() in _NAME_STOPWORDS:
        return None
    if s in {"女士", "先生"}:
        return None
    if not (2 <= len(s) <= 40):
        return None
    if re.fullmatch(r"[\u4e00-\u9fa5·]{2,8}", s):
        if any(w in s for w in _NAME_STOPWORDS):
            return None
        if any(w in s for w in _FILENAME_NOISE):
            return None
        return s
    # 英文姓名：首字母大写，避免把 resume / cv 当成姓名
    if re.fullmatch(r"[A-Z][A-Za-z .'\-]{1,40}", s):
        return s
    return None


def extract_name_from_text(text: str) -> str | None:
    """从简历正文提取姓名；优先“姓名：”，其次前 5 行的独立姓名行。"""
    if not text:
        return None
    normalized = text.replace("\u3000", " ").replace("\xa0", " ")
    for m in _NAME_LABEL_RE.finditer(normalized):
        name = _valid_name(m.group(1))
        if name:
            return name
    lines = [ln.strip() for ln in normalized.splitlines() if ln.strip()]
    for i, ln in enumerate(lines[:5]):
        if re.fullmatch(r"[\u4e00-\u9fa5·]{2,4}", ln):
            name = _valid_name(ln)
            if not name:
                continue
            # 首行姓名必须有上下文（电话/性别/求职/工作…），避免把“乱码”当姓名
            context = " ".join(lines[i + 1 : i + 3])
            if re.search(r"电话|手机|邮箱|性别|年龄|求职|工作|教育|期望|经验|微信", context):
                return name
    return None


def extract_name_from_filename(filename: str) -> str:
    """从文件名提取姓名；识别“【岗位_地点】姓名 年限.pdf”等常见命名。"""
    stem = (filename or "").rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    stem = re.sub(r"\.[A-Za-z0-9]{1,6}$", "", stem).strip()
    # 【...】/[...] 前缀：姓名通常在右括号之后
    if "】" in stem:
        stem = stem.split("】", 1)[1]
    stem = re.sub(r"【[^】]*】|\[[^\]]*\]|（[^）]*）|\([^)]*\)", " ", stem)
    stem = re.sub(r"\b(19|20)\d{2}\b", " ", stem)
    stem = re.sub(r"\d+\s*年", " ", stem)
    stem = re.sub(r"\d+\s*[-~—]\s*\d+\s*[kK千]", " ", stem)
    # 先匹配“X女士 / X先生”
    for m in re.finditer(r"([\u4e00-\u9fa5]{1,2}(?:女士|先生))", stem):
        name = _valid_name(m.group(1))
        if name:
            return name
    parts = [p.strip() for p in re.split(r"[\s\-_—·]+", stem) if p.strip()]
    candidates: list[str] = []
    for p in parts:
        name = _valid_name(p)
        if name:
            candidates.append(name)
    if candidates:
        candidates.sort(key=lambda x: (0 if 2 <= len(x) <= 3 else 1, len(x)))
        return candidates[0]
    return ""


def extract_candidate_name(text: str, filename: str) -> str:
    """正文优先、文件名兜底；都提取不到时返回占位名，绝不返回错误片段。"""
    return (
        extract_name_from_text(text)
        or extract_name_from_filename(filename)
        or "未命名候选人"
    )


def parse_resume(content: bytes, filename: str) -> str:
    """按扩展名解析简历为纯文本。

    Args:
        content: 文件原始字节。
        filename: 原始文件名，用于判断扩展名。

    Returns:
        解析后的纯文本；解析失败时降级为空串或尽力按文本解码，绝不抛异常。
    """
    name = (filename or "").lower().strip()

    try:
        if name.endswith(".pdf"):
            return _parse_pdf(content)
        if name.endswith(".docx"):
            return _parse_docx(content)
        # 纯文本（.txt / .md 或未识别扩展名）
        return _decode_text(content)
    except Exception:
        # 降级策略：解析异常时尝试按文本解码，仍失败则返回空串（共享知识 A5）
        return _decode_text(content)


def _parse_pdf(content: bytes) -> str:
    """使用 pdfplumber 逐页抽取文本。"""
    import pdfplumber

    parts: list[str] = []
    with pdfplumber.open(io.BytesIO(content)) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            if text:
                parts.append(text)
    return "\n".join(parts).strip()


def _parse_docx(content: bytes) -> str:
    """使用 python-docx 抽取段落文本。"""
    from docx import Document

    doc = Document(io.BytesIO(content))
    return "\n".join(p.text for p in doc.paragraphs).strip()


def _decode_text(content: bytes) -> str:
    """尽力按常见编码解码文本（UTF-8 / GB18030 / GBK）。"""
    for encoding in ("utf-8", "gb18030", "gbk"):
        try:
            return content.decode(encoding).strip()
        except UnicodeDecodeError:
            continue
    return content.decode("utf-8", errors="ignore").strip()
