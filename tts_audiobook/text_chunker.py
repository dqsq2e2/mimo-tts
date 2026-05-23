"""文本分块：按段落/句子边界将长文本切分为 TTS 友好块，支持章节检测。"""

import os
import re
from pathlib import Path

from .config import (
    DEFAULT_CHUNK_SIZE,
    MAX_CHUNK_SIZE,
    MIN_CHUNK_SIZE,
    TOKEN_ESTIMATE_PER_CHAR,
    CHAPTER_PATTERNS,
    SUPPORTED_INPUT_FORMATS,
    CHAPTER_FILE_PATTERN,
)


def estimate_tokens(text: str) -> int:
    """估算文本 token 数（中文约 1.5 tokens/字符）。"""
    return int(len(text) * TOKEN_ESTIMATE_PER_CHAR)


def chunk_text(text: str, chunk_size: int = DEFAULT_CHUNK_SIZE) -> list[str]:
    """将文本按段落/句子边界切分为指定大小的块。

    策略：
    1. 先按段落切分
    2. 超过 chunk_size 的段落按句子切分
    3. 合并过短的块（< MIN_CHUNK_SIZE）
    """
    chunk_size = min(chunk_size, MAX_CHUNK_SIZE)
    text = text.strip()
    if not text:
        return []

    # Step 1: 按段落切分
    paragraphs = _split_paragraphs(text)

    # Step 2: 超长段落按句子切分
    segments: list[str] = []
    for para in paragraphs:
        if len(para) <= chunk_size:
            if para.strip():
                segments.append(para.strip())
        else:
            sentences = _split_sentences(para)
            segments.extend(s for s in sentences if s.strip())

    # Step 3: 合并过短块 & 按 chunk_size 组装
    chunks = _assemble_chunks(segments, chunk_size)
    return chunks


def _split_paragraphs(text: str) -> list[str]:
    """按空行/换行符切分段落。"""
    # 先按连续换行分割
    parts = re.split(r"\n\s*\n", text)
    result: list[str] = []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        # 如果段落本身很短但是单行，保留
        lines = [l.strip() for l in p.split("\n") if l.strip()]
        # 合并回段落文本
        result.append("".join(lines))
    return result


def _split_sentences(text: str) -> list[str]:
    """按中文标点切分句子。"""
    # 在句末标点处切分，保留标点在句尾
    pattern = r'(.*?[。！？；：.!?;:]+)'
    parts = re.findall(pattern, text)
    remaining = re.sub(pattern, '', text).strip()

    sentences: list[str] = []
    for p in parts:
        p = p.strip()
        if p:
            sentences.append(p)
    if remaining:
        # 如果没有句末标点的大段文字，按逗号切分
        comma_parts = re.split(r'(?<=，|,)(?=\S)', remaining)
        for cp in comma_parts:
            cp = cp.strip()
            if cp:
                sentences.append(cp)

    return sentences


def _assemble_chunks(segments: list[str], chunk_size: int) -> list[str]:
    """将文本段组装为目标大小的块。"""
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0

    for seg in segments:
        seg_len = len(seg)

        # 跳过空段
        if seg_len == 0:
            continue

        # 如果当前块加上这段会超出限制，先结束当前块
        if current and current_len + seg_len > chunk_size:
            chunks.append("".join(current))
            current = []
            current_len = 0

        # 加入当前段
        current.append(seg)
        current_len += seg_len

    # 处理剩余
    if current:
        chunks.append("".join(current))

    # 后处理：合并过短的块到前一块
    merged: list[str] = []
    for chunk in chunks:
        if merged and len(chunk) < MIN_CHUNK_SIZE:
            merged[-1] = merged[-1] + chunk
        else:
            merged.append(chunk)

    return merged


def chunk_stats(chunks: list[str]) -> dict:
    """返回分块统计信息。"""
    lengths = [len(c) for c in chunks]
    return {
        "chunks": len(chunks),
        "total_chars": sum(lengths),
        "min_chars": min(lengths) if lengths else 0,
        "max_chars": max(lengths) if lengths else 0,
        "avg_chars": sum(lengths) / len(lengths) if lengths else 0,
        "est_tokens": estimate_tokens("".join(chunks)),
        "est_cost_input": estimate_tokens("".join(chunks))
        * 1.40  # ¥/1M tokens
        / 1_000_000,
    }


def detect_chapters(text: str) -> list[dict]:
    """检测文本中的章节边界。

    返回:
        [{"title": "第一章 深夜来客", "start": 0, "content": "..."}, ...]
        如果未检测到章节，返回单个章节 {"title": "全文", "start": 0, "content": text}
    """
    compiled = [re.compile(p, re.MULTILINE) for p in CHAPTER_PATTERNS]

    # 找出所有章节标题位置
    matches: list[tuple[int, str]] = []
    for pattern in compiled:
        for m in pattern.finditer(text):
            pos = m.start()
            # 只匹配行首（允许前面有少量空白）
            line_start = text.rfind("\n", 0, pos) + 1
            if pos - line_start <= 3:  # 前面最多 3 个空白字符
                if not any(abs(pos - existing) < 5 for existing, _ in matches):
                    matches.append((pos, m.group().strip()))

    if not matches:
        return [{"title": "全文", "start": 0, "content": text.strip()}]

    # 按位置排序
    matches.sort(key=lambda x: x[0])

    chapters: list[dict] = []
    for i, (pos, title) in enumerate(matches):
        next_pos = matches[i + 1][0] if i + 1 < len(matches) else len(text)
        content = text[pos:next_pos].strip()
        chapters.append({"title": title, "start": pos, "content": content})

    return chapters


def clean_text(text: str) -> str:
    """清洗输入文本，去掉影响 LLM 解析的噪音字符。

    - 去除分隔线（=== --- *** ~~~）
    - 合并连续空行为最多两个换行
    - 去除行首空白
    - 统一全角符号为半角（英文引号等）
    - 去掉孤立的 = 连接符
    """
    # 整行分隔线 → 单个换行
    text = re.sub(r'^[\W_]{3,}\s*$', '\n', text, flags=re.MULTILINE)
    # 行内长分隔符 → 空格
    text = re.sub(r'[\=\-\*]{3,}', ' ', text)
    # ⽹络⼩说常⻅的分隔符（全角/特殊）
    text = re.sub(r'[⿻□▪▌│┃║═━◆◇★☆♠♣♥♦●○◉◎◉▽▼△▲▷▶◁◀※⁂❧❦]', ' ', text)
    # 合并连续空行 — 最多两个换行
    text = re.sub(r'\n{3,}', '\n\n', text)
    # 去行首空格
    text = re.sub(r'^[ \t　]+', '', text, flags=re.MULTILINE)
    # 统一弯引号为直引号（中英文混用常见问题）
    text = text.replace(‘“’, ‘”’).replace(‘”’, ‘”’)
    text = text.replace(‘‘’, “’”).replace(‘’’, “’”)
    # 中文书名号内嵌的英文格式修正
    text = text.replace(‘（’, ‘(‘).replace(‘）’, ‘)’)
    text = text.replace(‘【’, ‘[‘).replace(‘】’, ‘]’)
    # 去掉独立 = 号（常见于网络小说分隔）
    text = re.sub(r’(?<!\w)=(?!\w)’, ‘’, text)
    return text.strip()


def load_text(source: str) -> str:
    """加载文本：支持单文件、目录（每章一个文件）。

    - 单文件 .txt / .md：读取全文本
    - 目录：按文件名排序，合并所有 .txt/.md 文件
    """
    path = Path(source)

    if path.is_file():
        return path.read_text(encoding="utf-8")

    if path.is_dir():
        # 收集所有文本文件，按章节文件名排序
        files: list[Path] = []
        for ext in SUPPORTED_INPUT_FORMATS:
            files.extend(path.glob(f"*{ext}"))
        # 尝试按章节号排序
        files.sort(key=_chapter_sort_key)
        parts: list[str] = []
        for f in files:
            parts.append(f.read_text(encoding="utf-8"))
        return "\n\n".join(parts)

    raise FileNotFoundError(f"未找到: {source}")


def _chapter_sort_key(p: Path) -> tuple:
    """提取文件名中的章节号用于排序。"""
    name = p.stem
    # 匹配所有数字
    nums = re.findall(r"\d+", name)
    if nums:
        return (0, int(nums[0]), name)
    return (1, 0, name)
