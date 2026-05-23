"""角色检测：用 LLM 自动识别文本中的角色，建立角色卡，分配音色。"""

import json
import os
import re
from typing import Optional

from openai import OpenAI

from .text_chunker import clean_text
from .config import (
    MIMO_BASE_URL,
    MIMO_TOKEN_PLAN_URL,
    MIMO_API_KEY_ENV,
    MIMO_TOKEN_PLAN_KEY_ENV,
    LLM_NORMAL_CHARACTER, LLM_TOKENPLAN_CHARACTER,
    CHARACTER_DETECT_MAX_CHARS,
    VOICE_ASSIGN_RULES,
    NARRATOR_VOICE,
)

CHARACTER_DETECT_PROMPT = """你是中文有声书导演。分析文本，识别所有角色并建立角色卡。同时为每个角色识别别称。

返回 JSON（只返回 JSON，文本中的双引号用反斜线转义）：

{
  "narrator_style": "旁白朗读风格（如：沉稳大气的男声，武侠叙事感，语速适中）",
  "characters": [
    {
      "name": "角色名（正式称呼）",
      "aliases": ["别称1", "别称2"],
      "gender": "男",
      "age": "青年",
      "personality": "性格关键词（如：深沉稳重、活泼调皮、表面冷漠内心炽热）",
      "role": "主角/反派/配角",
      "speaking_style": "朗读风格（如：语速缓慢声音低沉有磁性、语速快声音尖细带鼻音）"
    }
  ]
}

【铁律】
- 只列出有对话/出声的角色，路人/围观群众/店小二不建角色卡
- gender="男"或"女"，age="青年/中年/老年/少年/儿童"
- 不确定性别时根据名字、说话风格、称谓综合推断
- speaking_style 描述声音特征用于 TTS，不是性格描述
- narrator_style 需匹配全书基调（如武侠→大气沉稳、言情→温柔细腻）

【角色识别 — 按优先级】

1. 对话中自报姓名 → 最可靠
   "我是阿朱啊" → name=阿朱
   "老夫包不同" → name=包不同

2. 叙述中直接命名 + 对话标记 → 可靠
   "萧炎苦涩的道："……"" → name=萧炎
   "薰儿轻声道："……"" → name=萧薰儿（别称：薰儿）

3. 称谓/身份 + 对话 → 建立临时名，别称列表要全
   "中年男子看了一眼碑上的信息，语气漠然的将之公布了出来" → name=中年测验员
   "一旁的老者轻捋长须，面色平静的说道" → name=老者（神医）

4. 性别/年龄推断 → 无法确定名字时根据上下文：
   "八九岁年纪，粉嘟嘟的笑脸" → 女童/少年
   "苍老的声音" → 老年男性

5. 同一角色不得重复建卡 → 如果发现不同称呼指向同一人，合并
   例：文中"少女"、"薰儿"、"萧薰儿" → 同一人，只有一个角色卡

【别称识别 — 重要！】
同一角色在文中可能有多种称呼，必须全部列出到 aliases：
- 全名/简称：如"萧炎"也叫"炎儿""小炎子""萧家小子"
- 身份称谓：如"慕容复"也叫"公子""少主""表哥"
- 关系称呼：父亲叫"父亲""爹""老爷"
- 指代称呼：文中出现的"少女""青年""黑衣男子""老者"等指代，尽量关联到对应角色
- aliases 至少包含角色名本身

【示例】
角色 萧薰儿：文中称"薰儿""薰儿小姐""少女""萧薰儿"
→ "name":"萧薰儿","aliases":["萧薰儿","薰儿","薰儿小姐","少女"]

角色 老者（神医）：文中称"神医""老者""薛神医"
→ "name":"老者（神医）","aliases":["老者（神医）","神医","老者","薛神医"]

错误示例：
→ 把路人"围观群众"建了角色卡 ← 路人不需要
→ "name":"少女"但没有 aliases ← 缺少别称
"""


def detect_characters(
    text: str,
    api_key: Optional[str] = None,
    use_token_plan: bool = False,
    llm_config: Optional[dict] = None,
) -> dict:
    """用 LLM 分析文本，识别角色并建立角色卡。

    Returns:
        {
            "narrator_style": str,
            "characters": [
                {
                    "name": str,
                    "gender": str,
                    "age": str,
                    "personality": str,
                    "role": str,
                    "speaking_style": str,
                    "assigned_voice": str,     # 自动分配的音色 ID
                    "line_count_estimate": int,
                }
            ]
        }
    """
    llm_cfg = llm_config or {}
    key = llm_cfg.get("key") or api_key or os.environ.get(
        MIMO_TOKEN_PLAN_KEY_ENV if use_token_plan else MIMO_API_KEY_ENV, ""
    )
    base_url = llm_cfg.get("url") or (MIMO_TOKEN_PLAN_URL if use_token_plan else MIMO_BASE_URL)
    client = OpenAI(api_key=key, base_url=base_url)

    # 清洗文本
    text = clean_text(text)

    # 智能采样：取开头 + 中间 + 结尾各一部分，确保覆盖全书角色
    if len(text) <= CHARACTER_DETECT_MAX_CHARS:
        sample = text
    else:
        third = CHARACTER_DETECT_MAX_CHARS // 3
        sample = text[:third] + "\n...\n" + text[len(text)//2 - third//2:len(text)//2 + third//2] + "\n...\n" + text[-third:]

    model = llm_cfg.get("model") or (LLM_TOKENPLAN_CHARACTER if use_token_plan else LLM_NORMAL_CHARACTER)
    kwargs = dict(
        model=model,
        messages=[
            {"role": "user", "content": CHARACTER_DETECT_PROMPT + "\n\n文本：\n" + sample}
        ],
        max_completion_tokens=2048,
        temperature=0.3,
        response_format={"type": "json_object"},
    )
    # 思考模式：MiMo 默认关闭，第三方可通过 thinking 字段控制
    thinking = llm_cfg.get("thinking")
    if thinking == "enabled":
        kwargs["extra_body"] = {"thinking": {"type": "enabled"}}
        kwargs["reasoning_effort"] = llm_cfg.get("reasoning_effort") or "high"
    elif thinking == "disabled":
        kwargs["extra_body"] = {"thinking": {"type": "disabled"}}
    elif not llm_cfg.get("url") and not llm_cfg.get("key"):
        kwargs["extra_body"] = {"thinking": {"type": "disabled"}}
    response = client.chat.completions.create(**kwargs)

    msg = response.choices[0].message
    raw = (msg.content or "").strip()

    # 如果思考模式下 content 为空，尝试从 reasoning_content 提取 JSON
    if not raw and hasattr(msg, "reasoning_content") and msg.reasoning_content:
        rc = msg.reasoning_content.strip()
        # 尝试找 JSON 块：取最后一个 { 到最后一个 } 或 [ 到 ]
        m = re.search(r'\{[\s\S]*\}|\[[\s\S]*\]', rc)
        if m:
            raw = m.group(0)
        else:
            raw = rc

    if not raw:
        raise RuntimeError("模型返回空内容 — 如使用 DeepSeek 请关闭思考模式（JSON 输出与思考模式冲突）")

    # 清理可能的 markdown 代码块包裹 (```json ... ``` 或 ``` ... ```)
    import re as _re
    raw = _re.sub(r'^```(?:json|javascript|js)?\s*\n?', '', raw.strip())
    raw = _re.sub(r'\n?```\s*$', '', raw)
    raw = raw.strip()

    try:
        result = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"[detect-chars] JSON error: {e}")
        print(f"[detect-chars] Raw (first 500): {raw[:500]}")
        print(f"[detect-chars] Raw (last 200): ...{raw[-200:]}")
        raw = _repair_json(raw)
        result = json.loads(raw)

    # 校验并修正角色卡
    characters = _validate_and_fix_characters(result.get("characters", []), sample)
    # 自动派生别称（如 "慕容富/慕容复" → ["慕容富", "慕容复"]）
    for c in characters:
        c["aliases"] = _derive_aliases(c.get("name", ""))
    result["characters"] = characters

    # 自动分配音色
    _assign_voices(result)

    # 记录 token 用量
    result["_usage"] = {
        "prompt_tokens": response.usage.prompt_tokens,
        "completion_tokens": response.usage.completion_tokens,
        "total_tokens": response.usage.total_tokens,
    }

    return result


def _normalize_gender(raw: str) -> str:
    """标准化性别值，兼容 LLM 可能返回的各种格式。"""
    if not raw:
        return "男"
    g = raw.strip().lower()
    # 女性关键词
    if any(kw in g for kw in ["女", "female", "woman", "girl", "她", "f"]):
        return "女"
    # 男性关键词
    if any(kw in g for kw in ["男", "male", "man", "boy", "他", "m"]):
        return "男"
    return "男"


def _infer_gender_from_name(name: str) -> str:
    """从中文名推断性别（启发式规则，侧重名而非姓氏）。"""
    # 女性特征：名中用字、称谓
    female_patterns = [
        "雪", "芳", "丽", "娜", "婷", "娟", "花", "萍", "红", "美",
        "玲", "秀", "兰", "慧", "蓉", "莉", "燕", "敏", "静",
        "怡", "婉", "娇", "娥", "婵", "莺", "鸳", "钗", "鬟", "婢", "妾",
        "凤", "妃", "姬", "姐", "妹", "姑", "娘", "阿", "丫", "翠",
        "香", "荷", "菊", "梅", "杏", "桃", "柳", "莺", "燕",
        "瑶", "琪", "琳", "珊", "珍", "珠", "琴", "诗", "画",
        "素", "柔", "媚", "娇", "嫣", "妩", "婷",
        "黛", "玉", "蝶", "仙", "蕊", "萍", "蓝", "紫", "绯",
    ]
    # 男性特征：名中用字（排除常见姓氏）
    male_patterns = [
        "伟", "强", "勇", "军", "杰", "峰", "刚", "磊",
        "辉", "志", "龙", "飞", "超", "侠", "客", "将", "帅", "臣",
        "君", "侯", "相", "皇", "帝", "主", "兄", "弟", "爷", "叔", "伯",
        "公", "子", "武", "文", "国", "东", "海",
        "虎", "豹", "鹰", "鹏", "鸿", "剑", "刀", "石", "岩", "山",
        "豪", "霸", "雄", "猛", "威", "烈",
    ]

    # 给名字后半部分（名）更高权重
    given_name = name[1:] if len(name) >= 2 else name

    f_score = sum(1 for c in given_name if c in female_patterns)
    m_score = sum(1 for c in given_name if c in male_patterns)

    # 也检查全名中的女性专属词（阿X、X娘、X儿 等女性专属）
    if any(name.startswith(p) for p in ["阿"]) and not any(c in given_name for c in male_patterns):
        f_score += 2  # 阿朱、阿碧 → 大概率女性
    if any(name.endswith(p) for p in ["娘", "儿", "妾", "妃", "姬"]):
        f_score += 2

    if f_score > m_score:
        return "女"
    if m_score > f_score:
        return "男"
    return "男"


def _validate_and_fix_characters(characters: list[dict], text_sample: str = "") -> list[dict]:
    """校验并修正角色卡：标准化 gender、用名字推断修正、检查合理性。"""
    genders_seen = {"男": 0, "女": 0}
    for char in characters:
        raw_gender = char.get("gender", "")
        char["gender"] = _normalize_gender(raw_gender)
        genders_seen[char["gender"]] += 1

    # 如果全部都是同一性别，用名字推断修正
    if len(characters) >= 2 and (genders_seen["男"] == 0 or genders_seen["女"] == 0):
        for char in characters:
            inferred = _infer_gender_from_name(char.get("name", ""))
            if inferred != char["gender"]:
                char["gender"] = inferred
                char["_gender_fixed"] = True

    return characters


def _assign_voices(result: dict):
    """根据性别和年龄自动分配 MiMo 预置音色。"""
    for char in result.get("characters", []):
        gender = char.get("gender", "男")
        age = char.get("age", "青年")
        # 标准化 gender 以防 LLM 返回非预期值
        rules = VOICE_ASSIGN_RULES.get(gender, VOICE_ASSIGN_RULES["男"])
        voice = rules.get(age, rules.get("默认", "苏打"))
        char["assigned_voice"] = voice

    # 旁白音色：根据旁白风格描述智能匹配
    ns = result.get("narrator_style", "")
    if ns:
        guessed = _guess_narrator_voice(ns)
        if guessed:
            result["narrator_voice"] = guessed
        else:
            result["narrator_voice"] = NARRATOR_VOICE
    else:
        result["narrator_voice"] = NARRATOR_VOICE


# ── 合并检测：一次 LLM 调用完成角色识别 + 脚本分段 ──
COMBINED_PROMPT = """你是中文有声书导演。分析本章文本，识别新角色并拆分朗读片段。

【已知角色】（前面章节已识别的角色，本章可能继续出场）
{existing_chars}

返回 JSON（只返回 JSON，文本中的双引号用反斜线转义）：
{
  "narrator_style": "旁白风格",
  "new_characters": [{"name":"新角色","gender":"男","age":"青年","personality":"","role":"配角","speaking_style":""}],
  "updated_characters": [{"name":"已有角色名","gender":"男","age":"中年","personality":"变得更加沉稳","role":"主角","speaking_style":"低沉男声"}],
  "segments": [{"speaker":"角色名或旁白","text":"原文"}]
}

【角色更新说明】
- new_characters: 本章新出现的角色（之前章节没列出的）
- updated_characters: 本章揭示了已知角色的新信息时才填（性格变化、年龄增长、身份揭露等），包含该角色的完整字段，无变化则留空数组
- 已有角色列表中已列出的角色，segments 直接用他们的名字

══════════════════════════════════════
【最高准则：原文零丢失】
segments 所有 text 拼接 = 原文逐字！不概括、不省略、不添词、不改字。
══════════════════════════════════════

【角色处理】
- new_characters 只列本章新出场且有对话的**重要角色**（已知角色不重复列）
- 路人/围观者/店小二等一次性出场角色 → 不建立角色卡，segments 里标为"路人"
- "路人"不是角色卡中的角色，是一个通用标签，表示无法确定身份的说
- gender="男"或"女"，age="青年/中年/老年/少年/儿童"

【铁律：引号内内容绝不归旁白】
- 所有引号"…"「…」『…』包裹的文字，必然是某个人在说话，绝不归旁白
- 如果实在无法确定是谁说的 → 标为"路人"
- 旁白只包含：叙述、描写、动作、"XXX说/道："等引导语、心理活动提示语

【对话归属 — 按优先级精确判断】

1. "XXX说/道/问/答/喊/叫/笑/叹/怒/喝/骂/嚷/吼/冷声/低语/开口"
   → 后面的引号或对话内容属于 XXX

2. 引号"…"「…」『…』内的文字 → 向前找最近的"XXX说/道"
   - 引号前描述动作的文字（"XXX皱了皱眉"）→ 归旁白

3. 无引号对话：
   "XXX笑道你这家伙" → "你这家伙"归 XXX
   句末的"XXX道" → 前面的引号内容归 XXX

4. "XXX心想/暗想/心道/暗忖/寻思" → 归 XXX（内心独白）

5. 同一段多人对话必须分开：
   「"来了。"萧炎抬头。"嗯。"薰儿微笑。」→
   [萧炎:"来了。"] [旁白:"萧炎抬头。"] [薰儿:"嗯。"] [旁白:"薰儿微笑。"]

6. 对话中断后继续：
   「"我知道……"他顿了顿，"……是你。"」→
   [角色:"我知道……"] [旁白:"他顿了顿，"] [角色:"……是你。"]

7. 称呼推断说话人（重要！）：
   - 对话中出现"萧炎哥哥"→ 说话人是薰儿（她这样叫萧炎）
   - 自称"老夫/本座/为师"→ 判断对应角色
   - 对话口吻与已知角色性格匹配

8. 上下文场景推断（重要！）：
   - 同一场景/同一段连续对话 → 没明确换人时，维持上一段的说话人
   - 前文"两人交谈"→ 来回对话在两人之间切
   - 实在无法确定 → 标"旁白"

9. 书信/传音/系统提示/口诀/群众齐声 → 归"旁白"

【示例】
已知角色：萧炎、薰儿
输入：「萧炎苦笑："现在谁信我？"薰儿轻声道："我信。"」

正确：
[{"speaker":"旁白","text":"萧炎苦笑："},
 {"speaker":"萧炎","text":"现在谁信我？"},
 {"speaker":"旁白","text":"薰儿轻声道："},
 {"speaker":"薰儿","text":"我信。"}]

错误：
[{"speaker":"萧炎","text":"现在谁信我？我信。"}] ← 丢了旁白，薰儿的话也混进去了！
"""


def detect_and_parse(
    text: str,
    api_key: Optional[str] = None,
    use_token_plan: bool = False,
    existing_characters: list[dict] | None = None,
    llm_config: Optional[dict] = None,
) -> dict:
    """一次 LLM 调用：识别角色 + 脚本分段。

    支持增量：传入 existing_characters（已识别角色），LLM 只返回新角色。

    Returns:
        {
            "narrator_style": str,
            "characters": [{name, gender, age, ..., assigned_voice}],  # 全部角色
            "new_characters": [...],  # 仅本章新角色
            "segments": [{speaker, text, voice, style}],
            "_usage": {prompt_tokens, completion_tokens, total_tokens},
        }
    """
    llm_cfg = llm_config or {}
    key = llm_cfg.get("key") or api_key or os.environ.get(
        MIMO_TOKEN_PLAN_KEY_ENV if use_token_plan else MIMO_API_KEY_ENV, ""
    )
    base_url = llm_cfg.get("url") or (MIMO_TOKEN_PLAN_URL if use_token_plan else MIMO_BASE_URL)
    client = OpenAI(api_key=key, base_url=base_url)

    model = llm_cfg.get("model") or (LLM_TOKENPLAN_CHARACTER if use_token_plan else LLM_NORMAL_CHARACTER)

    # 构建已知角色描述（含完整信息，LLM 可以更新）
    existing = existing_characters or []
    if existing:
        parts = []
        for c in existing:
            parts.append(
                f'{c["name"]}（{c.get("gender","?")}·{c.get("age","?")}·{c.get("personality","?")}·{c.get("role","?")}·音色{c.get("assigned_voice","?")}）')
        existing_desc = "\n".join(parts) + "\n\n如果本章揭示了某个角色的新信息（性格变化、年龄增长、身份揭露等），请在 updated_characters 中返回该角色的最新完整信息。"
    else:
        existing_desc = "暂无（第一章，所有角色都是新角色）"

    text = clean_text(text)
    prompt = COMBINED_PROMPT.replace("{existing_chars}", existing_desc)

    kwargs = dict(
        model=model,
        messages=[
            {"role": "user", "content": prompt + "\n\n文本：\n" + text}
        ],
        max_completion_tokens=16384,  # 分段 JSON 很大，需要足够输出空间
        temperature=0.3,
        response_format={"type": "json_object"},
    )
    thinking = llm_cfg.get("thinking")
    if thinking == "enabled":
        kwargs["extra_body"] = {"thinking": {"type": "enabled"}}
    elif thinking == "disabled":
        kwargs["extra_body"] = {"thinking": {"type": "disabled"}}
    elif not llm_cfg.get("url") and not llm_cfg.get("key"):
        kwargs["extra_body"] = {"thinking": {"type": "disabled"}}
    response = client.chat.completions.create(**kwargs)

    msg = response.choices[0].message
    raw = (msg.content or "").strip()
    if not raw and hasattr(msg, "reasoning_content") and msg.reasoning_content:
        raw = msg.reasoning_content.strip()
    if not raw:
        raise RuntimeError("模型返回空内容")

    raw = _re.sub(r'^```(?:json|javascript|js)?\s*\n?', '', raw.strip())
    raw = _re.sub(r'\n?```\s*$', '', raw)
    raw = raw.strip()

    print(f"[detect-chars] Raw JSON ({len(raw)} chars): {raw[:600]}")
    try:
        result = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"[detect-chars] JSON error: {e}")
        print(f"[detect-chars] Raw tail: ...{raw[-200:]}")
        raw = _repair_json(raw)
        result = json.loads(raw)

    # 合并角色：已有角色 + 本章新角色 + 更新已有角色
    new_chars = result.get("new_characters", result.get("characters", []))
    updated_chars = result.get("updated_characters", [])
    new_chars = _validate_and_fix_characters(new_chars, text)
    updated_chars = _validate_and_fix_characters(updated_chars, text)
    for c in new_chars + updated_chars:
        c["aliases"] = _derive_aliases(c.get("name", ""))

    # 合并：已有角色（保留 history）→ 应用更新 → 加入新角色
    all_chars = {}
    for c in existing:
        all_chars[c["name"]] = dict(c)  # shallow copy
    # 应用更新：已有角色被同名覆盖
    for c in updated_chars:
        old = all_chars.get(c["name"], {})
        old_history = old.get("history", [])
        all_chars[c["name"]] = c
        c["history"] = old_history  # 保留成长记录
    # 加入新角色
    for c in new_chars:
        if c["name"] not in all_chars:
            all_chars[c["name"]] = c
    characters = list(all_chars.values())
    result["characters"] = characters
    result["new_characters"] = new_chars
    result["updated_characters"] = updated_chars

    # 分配音色
    _assign_voices(result)

    # 为 segments 填充音色
    char_map = {}
    for c in characters:
        char_map[c["name"]] = {"voice": c.get("assigned_voice", ""),
                                "style": c.get("speaking_style", ""),
                                "gender": c.get("gender", "")}
        for alias in c.get("aliases", []):
            if alias not in char_map:
                char_map[alias] = {"voice": c.get("assigned_voice", ""),
                                   "style": c.get("speaking_style", ""),
                                   "gender": c.get("gender", "")}

    from .script_parser import _fuzzy_match_character, _guess_voice_from_name

    nv = result.get("narrator_voice", NARRATOR_VOICE)
    ns = result.get("narrator_style", "")

    for seg in result.get("segments", []):
        name = seg.get("speaker", "旁白")
        if name in char_map:
            seg["voice"] = char_map[name]["voice"]
            seg["style"] = char_map[name]["style"]
        elif name != "旁白":
            matched = _fuzzy_match_character(name, char_map)
            if matched:
                seg["speaker"] = matched
                seg["voice"] = char_map[matched]["voice"]
                seg["style"] = char_map[matched]["style"]
            else:
                guessed = _guess_voice_from_name(name)
                seg["voice"] = guessed or nv
                seg["style"] = ns
        else:
            seg["voice"] = nv
            seg["style"] = ns

    # 完整性校验
    input_chars = len(text)
    output_chars = sum(len(s.get("text", "")) for s in result.get("segments", []))
    if input_chars > 0 and output_chars < input_chars * 0.9:
        lost_start = 0
        for s in result.get("segments", []):
            seg_text = s.get("text", "")
            idx = text.find(seg_text, lost_start)
            if idx >= 0:
                lost_start = idx + len(seg_text)
        remaining = text[lost_start:].strip()
        if remaining:
            result["segments"].append({"speaker": "旁白", "text": remaining,
                                       "voice": nv, "style": ns})

    result["_usage"] = {
        "prompt_tokens": response.usage.prompt_tokens,
        "completion_tokens": response.usage.completion_tokens,
        "total_tokens": response.usage.total_tokens,
    }

    return result


def _repair_json(raw):
    """Repair common JSON errors from LLM output. Primary fix: escape unescaped
    double-quotes inside text field values (LLM puts Chinese quotes in JSON)."""
    import re
    raw = raw.strip()
    # 0. Fix the #1 error: unescaped " inside "text":"..." values
    #    Scan for text fields and escape internal quotes
    result = []
    i = 0
    while i < len(raw):
        # Find next "text":" marker
        m = re.match(r'"text"\s*:\s*"', raw[i:])
        if m:
            result.append(m.group(0))
            i += m.end()
            # Scan the value until we find the closing " (followed by } or ,)
            j = i
            while j < len(raw):
                if raw[j] == '\\':
                    result.append(raw[i:j+2])
                    i = j + 2
                    j = i
                    continue
                if raw[j] == '"':
                    # Check if this is the closing quote
                    after = raw[j+1:j+5].lstrip()
                    if after and after[0] in '},]':
                        result.append(raw[i:j].replace('"', '\\"'))
                        result.append('"')
                        i = j + 1
                        break
                j += 1
            else:
                result.append(raw[i:])
                i = len(raw)
        else:
            result.append(raw[i])
            i += 1
    raw = ''.join(result)
    # 1. Remove trailing commas
    raw = re.sub(r',\s*}', '}', raw)
    raw = re.sub(r',\s*]', ']', raw)
    # 2. Quote unquoted keys
    raw = re.sub(r'([{,])\s*([a-zA-Z_]\w*)\s*:', r'\1"\2":', raw)
    # 3. Close unclosed braces/brackets
    open_b = raw.count('{')
    close_b = raw.count('}')
    if open_b > close_b:
        raw = raw.rstrip() + '\n}' * (open_b - close_b)
    open_sq = raw.count('[')
    close_sq = raw.count(']')
    if open_sq > close_sq:
        raw = raw.rstrip() + ']' * (open_sq - close_sq)
    return raw

def _derive_aliases(name: str) -> list[str]:
    """从角色名自动派生别称列表。"""
    aliases = [name]
    # 处理斜杠分隔：如 "慕容富/慕容复" → ["慕容富", "慕容复"]
    if "/" in name:
        parts = [p.strip() for p in name.split("/")]
        aliases.extend(p for p in parts if p and p not in aliases)
    # 处理括号内的别名：如 "老者（神医）" → ["老者", "神医"]
    import re
    bracket = re.findall(r'[（(]([^）)]+)[）)]', name)
    for b in bracket:
        aliases.append(b.strip())
    # 去除括号后作为别名：如 "漂亮女孩（慕容富女友）" → "漂亮女孩"
    clean = re.sub(r'[（(][^）)]*[）)]', '', name).strip()
    if clean and clean not in aliases:
        aliases.append(clean)
    return aliases


def _guess_narrator_voice(style: str):
    """根据旁白风格描述推断合适的音色。"""
    if not style:
        return None
    s = style.lower()
    # 男声关键词 → 白桦（沉稳中年男声）
    if any(kw in s for kw in ["男声", "男", "male", "沉稳", "大气", "武侠", "历史", "战争",
                                "厚重", "沧桑", "低沉", "磁性", "大叔", "爷们"]):
        return "白桦"
    # 青年男声关键词 → 苏打
    if any(kw in s for kw in ["少年", "青年男", "青春", "清朗", "活力"]):
        return "苏打"
    # 女声关键词
    if any(kw in s for kw in ["女声", "女", "female", "温柔", "甜美", "甜美", "少女",
                                "萝莉", "御姐", "成熟女", "知性"]):
        return "茉莉"
    return None


def save_characters(filepath: str, characters: list[dict], narrator_voice: str = "",
                   narrator_style: str = "", book_title: str = "", source_file: str = ""):
    """保存角色卡为书籍项目文件（JSON）。

    Args:
        filepath: 保存路径
        characters: 角色列表
        narrator_voice: 旁白音色
        narrator_style: 旁白风格
        book_title: 书名
        source_file: 源文件路径
    """
    from datetime import datetime
    data = {
        "book_title": book_title or "",
        "source_file": source_file or "",
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "narrator_voice": narrator_voice,
        "narrator_style": narrator_style,
        "characters": characters,
    }
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_characters(filepath: str) -> dict:
    """从 JSON 文件加载角色卡。"""
    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)
    return {
        "characters": data.get("characters", []),
        "narrator_voice": data.get("narrator_voice", ""),
        "narrator_style": data.get("narrator_style", ""),
    }


def format_character_cards(result: dict) -> str:
    """将角色检测结果格式化为可读的角色卡。"""
    lines = ["=" * 50, "  角色卡", "=" * 50]
    lines.append(f"  旁白音色: {result.get('narrator_voice', '茉莉')}")
    lines.append(f"  旁白风格: {result.get('narrator_style', '')}")
    lines.append("")
    for i, char in enumerate(result.get("characters", []), 1):
        lines.append(f"  [{i}] {char['name']}")
        lines.append(f"      性别: {char['gender']}  年龄: {char['age']}")
        lines.append(f"      性格: {char['personality']}")
        lines.append(f"      角色: {char['role']}")
        lines.append(f"      音色: {char['assigned_voice']}")
        lines.append(f"      风格: {char['speaking_style']}")
        lines.append("")
    lines.append("=" * 50)
    return "\n".join(lines)
