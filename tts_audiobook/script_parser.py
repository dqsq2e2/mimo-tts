"""脚本解析：将文本切分为带说话人标签的片段序列。

支持两种模式：
1. LLM 模式（默认）：用 mimo-v2-flash 精确识别说话人
2. 正则模式（备选）：基于中文对话标点快速解析，成本为 0
"""

import json
import os
import re
from typing import Optional

from openai import OpenAI

from .config import (
    MIMO_BASE_URL,
    MIMO_TOKEN_PLAN_URL,
    MIMO_API_KEY_ENV,
    MIMO_TOKEN_PLAN_KEY_ENV,
    MODEL_SCRIPT_PARSE,
)

SCRIPT_PARSE_PROMPT = """你是中文有声书朗读导演。将文本逐字拆分为朗读片段，标注每个片段属于哪个角色或旁白。

已知角色（含别称）：{characters}

返回 JSON（只返回 JSON，文本中的双引号用反斜线转义）：
{{"segments":[{{"speaker":"角色名或旁白","text":"原文片段"}}]}}

【铁律】
- 原文零丢失：所有 segments 的 text 拼接必须逐字等于输入原文
- 引号内如果是人物在说话 → 绝不归旁白
- speaker 必须用角色列表中的正式名，不可自创

【说话人判定 — 按优先级】

1. 明确标记（最优先）：
   "XXX说/道/问/答/喊/叫/开口/冷声/低语/怒道/笑道/叹道/骂道"
   → 紧接的引号内容必定是 XXX 说的，不可推翻
   例："慕容富开口道："你是谁？"" → speaker=慕容富

2. 角色名 + 动作 → 该角色的对话：
   "薰儿柔声道："萧炎哥哥。"" → speaker=萧薰儿
   "老者捋须道："此言差矣。"" → speaker=老者

3. 称呼推断：对话内容中的称呼暴露说话人身份
   "萧炎哥哥" → 薰儿说的（只有她这样叫）
   "少主" → 家臣说的
   "复儿" → 母亲说的

4. 场景继承：同一场景、同一段落内，没有明确换人标记 → 维持上一段的说话人

5. 代词/指代推断：无"XXX说"但有代词或称呼
   "少女轻叹一声：'……'" → 检查"少女"是否在角色别称中
   "她苦笑道：'……'" → 前文找"她"指代谁
   "黑衣人道：'……'" → 检查"黑衣人"是否在角色别称中

6. 以上方法都用尽仍无法确定 → 标"未知"（不标旁白）

【旁白范围】
纯叙述、环境描写、动作描写、心理描写、"XXX道："引导语

【特殊引号 — 具体情况分析】
- 短引号（1-3字）无说话动词 → 强调/引用，归旁白
  例："天才"少年、"借"来的、"废材"逆袭
- 长引号（完整句子）→ 大概率是对话，继续寻找说话人
- 象声词："嘀……嘀……"、"嗤——" → 归旁白
- 心理独白：心想"..."、暗想"..." → 归旁白（内心活动，非出声）

【示例】
输入："萧炎哥哥。"薰儿停下脚步。"你还好吗？"
输出：{{"segments":[{{"speaker":"萧薰儿","text":"萧炎哥哥。"}},{{"speaker":"旁白","text":"薰儿停下脚步。"}},{{"speaker":"萧薰儿","text":"你还好吗？"}}]}}

输入："慕容富开口道："你是谁？""阿朱一愣："我是阿朱啊。""
输出：{{"segments":[{{"speaker":"旁白","text":"慕容富开口道："}},{{"speaker":"慕容富","text":"你是谁？"}},{{"speaker":"旁白","text":"阿朱一愣："}},{{"speaker":"阿朱","text":"我是阿朱啊。"}}]}}
"""


def parse_script_llm(
    text: str,
    characters: list[dict],
    api_key: Optional[str] = None,
    use_token_plan: bool = False,
    llm_config: Optional[dict] = None,
) -> list[dict]:
    """用 LLM 将文本解析为说话人片段序列。

    Args:
        text: 完整文本
        characters: 角色列表（来自 detect_characters）

    Returns:
        [{speaker: str, text: str, voice: str, style: str}, ...]
    """
    llm_cfg = llm_config or {}
    key = llm_cfg.get("key") or api_key or os.environ.get(
        MIMO_TOKEN_PLAN_KEY_ENV if use_token_plan else MIMO_API_KEY_ENV, ""
    )
    base_url = llm_cfg.get("url") or (MIMO_TOKEN_PLAN_URL if use_token_plan else MIMO_BASE_URL)
    client = OpenAI(api_key=key, base_url=base_url)

    # 构建角色描述：名字 + 别称
    char_parts = []
    for c in characters:
        aliases = c.get("aliases", [])
        if aliases:
            char_parts.append(f'{c["name"]}（也称：{"、".join(a for a in aliases if a != c["name"])}）')
        else:
            char_parts.append(c["name"])
    prompt = SCRIPT_PARSE_PROMPT.format(
        characters=", ".join(char_parts + ["旁白"])
    )

    model = llm_cfg.get("model") or ("mimo-v2.5" if use_token_plan else MODEL_SCRIPT_PARSE)
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "user", "content": prompt + "\n\n文本：\n" + text}
        ],
        max_completion_tokens=4096,
        temperature=0.1,
        response_format={"type": "json_object"},
        extra_body={"thinking": {"type": "disabled"}},
    )

    msg = response.choices[0].message
    raw = (msg.content or "").strip()
    if not raw and hasattr(msg, "reasoning_content") and msg.reasoning_content:
        raw = msg.reasoning_content.strip()
    if not raw:
        raise RuntimeError("模型返回空内容")

    if raw.startswith("```"):
        lines = raw.split("\n")
        raw = "\n".join(lines[1:]) if lines[0].startswith("```") else raw
        if raw.endswith("```"):
            raw = raw[:-3]
    raw = raw.strip()

    try:
        data = json.loads(raw)
        segments = data.get("segments", data if isinstance(data, list) else [])
    except json.JSONDecodeError as e:
        print(f"[parse_llm] JSON error: {e}")
        from .character_detector import _repair_json
        try:
            raw = _repair_json(raw)
            data = json.loads(raw)
            print(f"[parse_llm] Repaired OK")
        except Exception as e2:
            print(f"[parse_llm] Repair failed: {e2}, using regex fallback")
            raise  # let outer handler catch and use regex
    segments = data.get("segments", data if isinstance(data, list) else [])
    input_chars = len(text)
    output_chars = sum(len(s.get("text", "")) for s in segments)
    if input_chars > 0 and output_chars < input_chars * 0.9:
        # 丢失文本 > 10%，从原文末尾补齐
        lost_start = 0
        for s in segments:
            seg_text = s.get("text", "")
            idx = text.find(seg_text, lost_start)
            if idx >= 0:
                lost_start = idx + len(seg_text)
        remaining = text[lost_start:].strip()
        if remaining:
            segments.append({"speaker": "旁白", "text": remaining, "voice": "", "style": ""})

    # 建立角色名 → {voice, style} 映射
    char_map: dict[str, dict] = {}
    for c in characters:
        char_map[c["name"]] = {
            "voice": c.get("assigned_voice", ""),
            "style": c.get("speaking_style", ""),
            "gender": c.get("gender", ""),
        }
        # 注册所有别称
        for alias in c.get("aliases", []):
            if alias not in char_map:
                char_map[alias] = {
                    "voice": c.get("assigned_voice", ""),
                    "style": c.get("speaking_style", ""),
                    "gender": c.get("gender", ""),
                    "_alias_for": c["name"],
                }

    # 填充音色和风格（含模糊匹配）
    for seg in segments:
        name = seg.get("speaker", "旁白")
        if name in char_map:
            seg["voice"] = char_map[name]["voice"]
            seg["style"] = char_map[name]["style"]
        else:
            matched = _fuzzy_match_character(name, char_map)
            if matched:
                seg["speaker"] = matched
                seg["voice"] = char_map[matched]["voice"]
                seg["style"] = char_map[matched]["style"]
            else:
                # 兜底：从称谓推断音色
                guessed = _guess_voice_from_name(name)
                seg["voice"] = guessed or ""
                seg["style"] = ""

    llm_usage = {
        "prompt_tokens": response.usage.prompt_tokens,
        "completion_tokens": response.usage.completion_tokens,
        "total_tokens": response.usage.total_tokens,
    }
    return segments, llm_usage


def _fuzzy_match_character(name: str, char_map: dict):
    """模糊匹配说话人名到角色卡中的角色名。

    处理：别名、曾用名、简称、LLM 识别偏差等问题。
    """
    if not name or name == "旁白":
        return None

    char_names = list(char_map.keys())

    # 1. 精确包含：角色名包含说话人名 或 说话人名包含角色名
    for cn in char_names:
        if cn in name or name in cn:
            return cn

    # 2. 共享字符：去掉姓氏后比较
    name_core = name[1:] if len(name) >= 2 else name
    for cn in char_names:
        cn_core = cn[1:] if len(cn) >= 2 else cn
        if len(name_core) >= 1 and len(cn_core) >= 1:
            if name_core == cn_core:
                return cn
            common = set(name_core) & set(cn_core)
            if common and len(name_core) <= 3 and len(cn_core) <= 3:
                return cn

    # 3. 同姓：如 "慕容复" vs "慕容富"
    if len(name) >= 2 and len(char_names) >= 1:
        surname = name[0] if len(name) <= 3 else name[:2]
        for cn in char_names:
            cn_surname = cn[0] if len(cn) <= 3 else cn[:2]
            if surname == cn_surname and surname not in {"小", "老", "阿"}:
                return cn

    return None


# ── 称谓 → 音色兜底映射 ──
# 当 LLM 识别的说话人名不在角色卡中时，根据称谓推断音色
GENERIC_SPEAKER_VOICE = {
    # 男性称谓
    "老者": "白桦", "老人": "白桦", "老头": "白桦", "老翁": "白桦",
    "青年": "苏打", "少年": "苏打", "男孩": "苏打", "男子": "苏打",
    "中年男子": "白桦", "中年人": "白桦", "大汉": "白桦",
    "公子": "苏打", "少爷": "苏打", "少主": "苏打",
    "先生": "白桦", "师傅": "白桦", "师父": "白桦",
    "侠客": "苏打", "英雄": "苏打", "高手": "苏打",
    "汉子": "白桦", "壮汉": "白桦", "大佬": "白桦",
    "父亲": "白桦", "爹": "白桦", "叔": "白桦",
    "兄": "苏打", "弟": "苏打",
    # 女性称谓
    "女孩": "冰糖", "少女": "冰糖", "女子": "冰糖",
    "中年女子": "茉莉", "妇人": "茉莉", "美妇": "茉莉",
    "小姐": "冰糖", "姑娘": "冰糖", "丫鬟": "冰糖",
    "母亲": "茉莉", "娘": "茉莉", "夫人": "茉莉",
    "姐": "冰糖", "妹": "冰糖", "婆婆": "茉莉",
    "小女孩": "冰糖", "老妇人": "茉莉", "老太": "茉莉",
}


def _find_speaker_in_text(text: str, char_map: dict, speak_verbs: str) -> str:
    """在文本中查找最靠近末尾的说话人（角色名 + 说/道）。

    逐个角色名独立搜索，避免 finditer 的重叠匹配问题。
    如 "萧炎的颓废，萧薰儿认真的道" → 返回 "萧薰儿"（最靠近末尾）。
    """
    if not text or not char_map:
        return "旁白"
    best_pos = -1
    best_dist = 99999  # 名字到动词的距离（越小越近）
    best_speaker = "旁白"
    # 扩展搜索：除了角色名本身，也匹配角色的部分名（如 "围观少年甲" → 搜索 "少年甲", "围观少年"）
    expanded_names = set(char_map.keys())
    for name in list(char_map.keys()):
        if len(name) >= 4:
            expanded_names.add(name[-3:])  # 后缀 3 字
            expanded_names.add(name[-2:])  # 后缀 2 字
    for name in expanded_names:
        pos = text.rfind(name)
        if pos < 0:
            continue
        after = text[pos + len(name):]
        m = re.search(speak_verbs, after)
        if m:
            verb_pos = pos + len(name) + m.start()
            dist = m.start()  # 名字末尾到动词的距离
            # 动词位置越靠后越好；同位置时距离越近越好
            if verb_pos > best_pos or (verb_pos == best_pos and dist < best_dist):
                best_pos = verb_pos
                best_dist = dist
                best_speaker = name
    # 如果匹配到的是后缀名，映射回原始角色名
    if best_speaker != "旁白" and best_speaker not in char_map:
        for cn in char_map:
            if cn.endswith(best_speaker) or best_speaker in cn:
                best_speaker = cn
                break
    return best_speaker


def _guess_voice_from_name(name: str):
    """从称谓/描述推断音色（兜底策略）。"""
    if not name or name == "旁白":
        return None
    # 精确匹配
    if name in GENERIC_SPEAKER_VOICE:
        return GENERIC_SPEAKER_VOICE[name]
    # 子串匹配
    for kw, voice in GENERIC_SPEAKER_VOICE.items():
        if kw in name:
            return voice
    return None


def parse_script_regex(text: str, characters: list[dict]) -> list[dict]:
    """用正则解析中文对话，将文本拆分为 {说话人, 内容} 片段。

    识别模式：
    - 角色名+说/道/问/喊/叫/答/笑/叹/怒/喝/骂/嚷/吼/言："对话"
    - "对话" 角色名+说/道/问...
    - 对话在引号（"" ''「」『』）中，前面有角色名出现
    - 其余为旁白
    """
    char_map: dict[str, dict] = {}
    for c in characters:
        char_map[c["name"]] = {
            "voice": c.get("assigned_voice", ""),
            "style": c.get("speaking_style", ""),
        }

    if not char_map:
        return [{"speaker": "旁白", "text": text, "voice": "", "style": ""}]

    names_pattern = "|".join(re.escape(n) for n in char_map.keys())
    # 说话动词
    speak_verbs = r"(?:说道|问道|喊道|叫道|答道|笑道|叹道|怒道|喝道|骂道|言道|开口|说|道|问|喊|叫|答|叹|骂|嚷|吼)"

    segments: list[dict] = []
    paragraphs = re.split(r"\n+", text)

    for para in paragraphs:
        para = para.strip()
        if not para:
            continue

        # 尝试将段落拆分为更细的对话片段
        sub_segments = _split_paragraph_by_speaker(para, names_pattern, speak_verbs, char_map)
        segments.extend(sub_segments)

    return segments


def _split_paragraph_by_speaker(
    para: str, names_pattern: str, speak_verbs: str, char_map: dict
) -> list[dict]:
    """在一个段落内识别说话人切换，拆分为多个片段。"""
    if not names_pattern:
        return [{"speaker": "旁白", "text": para, "voice": "", "style": ""}]

    # 策略：在段落中找到引号包裹的对话，向前查找最近的说话人
    # 引号模式
    quote_patterns = [
        r'["“]([^"”]+)["”]',   # 中文双引号 "..." 或 "..."
        r'“([^”]+)”',            # "..."
        r'[「]([^」]+)[」]',        # 「...」
        r'[『]([^』]+)[』]',        # 『...』
        r'‘([^’]+)’',            # '...'
    ]

    # 找到所有引号位置（去重：相同位置的引号只保留一个）
    quote_spans = []
    seen = set()
    for pattern in quote_patterns:
        for m in re.finditer(pattern, para):
            key = (m.start(), m.end())
            if key not in seen:
                seen.add(key)
                quote_spans.append((m.start(), m.end(), m.group(1)))

    if not quote_spans:
        # 没有引号的段落 → 旁白
        return [{"speaker": "旁白", "text": para, "voice": "", "style": ""}]

    # 按引号拆分段落：引号前文本 → 旁白，引号内 → 角色
    result = []
    last_end = 0
    last_speaker = "旁白"  # 同一段落内继承说话人

    for q_start, q_end, quote_text in sorted(quote_spans):
        # 单字引号判断：前后有说话动词才当对话，否则是强调词（如从各派"借"来）
        if len(quote_text) <= 2:
            ctx_before = para[max(0,q_start-20):q_start]
            ctx_after = para[q_end:min(len(para),q_end+20)]
            near_verb = bool(re.search(speak_verbs, ctx_before)) or bool(re.search(speak_verbs, ctx_after))
            if not near_verb:
                continue  # 不是对话，跳过
        # 引号前的文本永远是旁白（叙述描写）
        before = para[last_end:q_start].strip()
        if before:
            result.append({"speaker": "旁白", "text": before, "voice": "", "style": ""})

        # 检测说话人：先看引号前，再看引号后，最后继承上一句
        speaker = _find_speaker_in_text(before, char_map, speak_verbs)
        if speaker == "旁白":
            after_quote = para[q_end:q_end + 50]
            speaker = _find_speaker_in_text(after_quote, char_map, speak_verbs)
        if speaker == "旁白" and last_speaker != "旁白":
            # 同一段内，前一句对话的角色 → 当前也很可能是同一人
            # 如 "萧薰儿道：'...' 话到此处，少女...：'...'" → 第二个引号仍属萧薰儿
            speaker = last_speaker

        voice = char_map.get(speaker, {}).get("voice", "")
        style = char_map.get(speaker, {}).get("style", "")
        result.append({"speaker": speaker, "text": quote_text, "voice": voice, "style": style})
        last_speaker = speaker
        last_end = q_end

    # 剩余文本 → 旁白
    after = para[last_end:].strip()
    if after:
        result.append({"speaker": "旁白", "text": after, "voice": "", "style": ""})

    return result if result else [{"speaker": "旁白", "text": para, "voice": "", "style": ""}]


def parse_script(
    text: str,
    characters: list[dict],
    narrator_style: str = "",
    narrator_voice: str = "",
    use_llm: bool = True,
    api_key: Optional[str] = None,
    use_token_plan: bool = False,
    llm_config: Optional[dict] = None,
) -> list[dict]:
    """解析脚本，返回带音色标签的片段列表。

    Returns:
        [
            {
                "speaker": str,
                "text": str,
                "voice": str,       # MiMo 音色 ID
                "style": str,       # TTS 风格描述
            },
            ...
        ]
    """
    # 正则解析（零成本）
    segments = parse_script_regex(text, characters)
    llm_usage = None

    # 仅当用户明确勾选「LLM 脚本解析」时才使用 LLM
    if use_llm and api_key:
        try:
            segments, llm_usage = parse_script_llm(text, characters, api_key, use_token_plan=use_token_plan, llm_config=llm_config)
        except Exception:
            pass  # LLM 失败则保持正则结果

    # 模糊匹配 + 填充默认音色
    full_map = {}
    for c in characters:
        full_map[c["name"]] = {
            "voice": c.get("assigned_voice", ""),
            "style": c.get("speaking_style", ""),
            "gender": c.get("gender", ""),
        }
    for seg in segments:
        name = seg.get("speaker", "旁白")
        if name != "旁白" and name not in full_map:
            matched = _fuzzy_match_character(name, full_map)
            if matched:
                seg["speaker"] = matched
                seg["voice"] = full_map[matched]["voice"]
                seg["style"] = full_map[matched]["style"]
            else:
                guessed = _guess_voice_from_name(name)
                if guessed:
                    seg["voice"] = guessed
        if seg["speaker"] == "旁白" or not seg.get("voice"):
            seg["voice"] = narrator_voice
            seg["style"] = narrator_style

    # ── 完整性校验：检查文本是否丢失 ──
    input_chars = len(text)
    output_chars = sum(len(s.get("text", "")) for s in segments)
    if input_chars > 0 and output_chars < input_chars * 0.9:
        # 丢失超过 10%：末尾追加被截断的文本
        lost = text[output_chars:]
        if lost.strip():
            segments.append({
                "speaker": "旁白",
                "text": lost.strip(),
                "voice": narrator_voice,
                "style": narrator_style,
            })

    return segments, llm_usage
