"""简历解析：PDF / DOCX / 纯文本，解析失败降级不阻塞建会话。"""
import io


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
