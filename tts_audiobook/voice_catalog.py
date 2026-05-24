"""Curated built-in voice catalog sourced from the local IndexTTS pack."""

from __future__ import annotations

from pathlib import Path
from typing import Any


PACK_ROOT = Path(__file__).resolve().parents[2] / "IndexTTS-2几百款热门克隆音色包"
SHARED_VOICES = Path(__file__).resolve().parent.parent / "voices"


CURATED_VOICES: list[dict[str, str]] = [
    {"id": "narrator_male_documentary", "name": "男声纪录旁白", "gender": "男", "age": "中年", "scene": "旁白/纪录", "style": "沉稳、专业、叙事感", "path": "不同年龄人群音色/中年-男声/男-专题 纪录 旁白.wav"},
    {"id": "narrator_male_literary", "name": "文学旁白男声", "gender": "男", "age": "中年", "scene": "旁白/文学", "style": "舒展、清晰、故事感", "path": "不同年龄人群音色/热门音色/文学旁白解说（男声）.wav"},
    {"id": "narrator_female_magnetic", "name": "中音磁性女声旁白", "gender": "女", "age": "青年", "scene": "旁白/情感", "style": "磁性、温柔、耐听", "path": "不同年龄人群音色/热门音色/中音磁性女声旁白.wav"},
    {"id": "male_story_taiwan", "name": "台湾男青年故事讲述", "gender": "男", "age": "青年", "scene": "旁白/都市", "style": "自然、亲近、讲述感", "path": "不同年龄人群音色/热门音色/台湾男青年音故事讲述.wav"},
    {"id": "male_warm_young", "name": "温柔青年男声", "gender": "男", "age": "青年", "scene": "男主/旁白", "style": "温暖、柔和、干净", "path": "不同年龄人群音色/热门音色/温柔青年音.wav"},
    {"id": "male_magnetic", "name": "磁性男声", "gender": "男", "age": "青年", "scene": "男主/旁白", "style": "磁性、稳定、有质感", "path": "不同年龄人群音色/热门音色/磁性男声.wav"},
    {"id": "male_xiake", "name": "少年侠客", "gender": "男", "age": "少年", "scene": "少年/武侠", "style": "清亮、少年气、侠气", "path": "不同年龄人群音色/热门音色/少年侠客.wav"},
    {"id": "male_emperor", "name": "青年帝王霸总", "gender": "男", "age": "青年", "scene": "男主/权谋", "style": "强势、低沉、压迫感", "path": "不同年龄人群音色/热门音色/君阳-青年帝王霸总.wav"},
    {"id": "male_calm_orthodox", "name": "清冷正派男声", "gender": "男", "age": "中年", "scene": "师尊/正派", "style": "清冷、端正、克制", "path": "不同年龄人群音色/中年-男声/男-清冷、正派.wav"},
    {"id": "male_warm_middle", "name": "温暖中年男声", "gender": "男", "age": "中年", "scene": "父亲/长辈", "style": "和蔼、温暖、中年", "path": "不同年龄人群音色/中年-男声/男-和蔼、温暖、中年.wav"},
    {"id": "male_deep_elder", "name": "醇厚中老年男声", "gender": "男", "age": "老年", "scene": "长者/旁白", "style": "低沉、醇厚、娓娓道来", "path": "不同年龄人群音色/老年/男-淡然娓娓道来 醇厚.wav"},
    {"id": "male_hoarse_elder", "name": "沙哑老年男声", "gender": "男", "age": "老年", "scene": "老人/反派", "style": "沙哑、慢速、沧桑", "path": "不同年龄人群音色/老年/男-沙沙的老年音 慢速.wav"},
    {"id": "female_gentle_senior", "name": "温暖老年女声", "gender": "女", "age": "老年", "scene": "祖母/长辈", "style": "和蔼、温暖、亲切", "path": "不同年龄人群音色/老年/女-和蔼可亲、温暖.wav"},
    {"id": "female_shijie", "name": "温柔师姐", "gender": "女", "age": "青年", "scene": "女主/师姐", "style": "温柔、亲和、中文", "path": "不同年龄人群音色/热门音色/叶子温柔师姐-中文.wav"},
    {"id": "female_soft_sister", "name": "温软姐姐", "gender": "女", "age": "青年", "scene": "女主/姐姐", "style": "柔和、细腻、有质感", "path": "不同年龄人群音色/热门音色/温软姐姐 柔和质感.wav"},
    {"id": "female_sweet_girl", "name": "甜美少女音", "gender": "女", "age": "少女", "scene": "少女/女主", "style": "甜美、明亮、活泼", "path": "不同年龄人群音色/热门音色/甜美少女音.wav"},
    {"id": "female_lively", "name": "活泼小姐", "gender": "女", "age": "青年", "scene": "少女/配角", "style": "活泼、轻快、明亮", "path": "不同年龄人群音色/热门音色/活泼小姐.wav"},
    {"id": "female_yujie", "name": "御姐", "gender": "女", "age": "青年", "scene": "御姐/反派", "style": "成熟、冷艳、有气场", "path": "不同年龄人群音色/热门音色/御姐.wav"},
    {"id": "female_loli", "name": "可爱小萝莉", "gender": "女", "age": "儿童", "scene": "儿童/少女", "style": "可爱、清脆、稚嫩", "path": "不同年龄人群音色/热门音色/可爱小萝莉.wav"},
    {"id": "child_boy", "name": "小男孩", "gender": "男", "age": "儿童", "scene": "儿童", "style": "童真、明亮、自然", "path": "不同年龄人群音色/孩童/男-6-8岁的小男孩.wav"},
    {"id": "child_girl_sweet", "name": "甜美女孩", "gender": "女", "age": "儿童", "scene": "儿童", "style": "甜美、清脆、稚嫩", "path": "不同年龄人群音色/孩童/女-甜美 清脆 稚嫩.wav"},
    {"id": "female_cold_shijie", "name": "冷艳师姐", "gender": "女", "age": "青年", "scene": "女配/反派", "style": "清冷、妩媚、锋利", "path": "不同情绪音色/女-冷艳、师姐、妩媚.wav"},
    {"id": "male_humorous", "name": "幽默滑头男声", "gender": "男", "age": "青年", "scene": "喜剧/配角", "style": "幽默、滑头、中音", "path": "不同情绪音色/男-中音、幽默、滑头.wav"},
    {"id": "male_villain", "name": "纨绔青年男声", "gender": "男", "age": "青年", "scene": "反派/纨绔", "style": "嚣张、轻浮、青年", "path": "不同情绪音色/男-嚣张、纨绔、青年.wav"},
    # ── 扩展音色：覆盖更广角色画像 ──
    # 男性
    {"id": "male_refined_gentle", "name": "儒雅温柔男声", "gender": "男", "age": "青年", "scene": "书生/才子", "style": "儒雅、温柔、体贴", "path": "不同情绪音色/男-儒雅、温柔、体贴.wav"},
    {"id": "male_carefree", "name": "潇洒不羁男声", "gender": "男", "age": "青年", "scene": "侠客/浪子", "style": "潇洒、风流、不羁", "path": "不同情绪音色/男-潇洒不羁、风流倜傥.wav"},
    {"id": "male_scheming", "name": "心思深沉男声", "gender": "男", "age": "中年", "scene": "谋士/反派", "style": "深沉、算计、阴郁", "path": "不同年龄人群音色/中年-男声/男-中音、心思深沉.wav"},
    {"id": "male_cold_arrogant", "name": "清冷傲慢男声", "gender": "男", "age": "青年", "scene": "反派/天才", "style": "清冷、刻薄、傲慢", "path": "不同情绪音色/男-清冷刻薄、傲慢自大.wav"},
    {"id": "male_thug", "name": "流氓地痞男声", "gender": "男", "age": "中年", "scene": "反派/市井", "style": "嚣张、粗鲁、流气", "path": "不同年龄人群音色/中年-男声/流氓地痞-中音、嚣张、流氓.wav"},
    {"id": "male_strict_elder", "name": "古板严肃男声", "gender": "男", "age": "老年", "scene": "长老/师尊", "style": "古板、严肃、威严", "path": "不同年龄人群音色/中年-男声/男-古板严肃、低音.wav"},
    {"id": "male_weathered_elder", "name": "沧桑岁月男声", "gender": "男", "age": "老年", "scene": "老者/前辈", "style": "沧桑、沙哑、岁月沉淀", "path": "不同年龄人群音色/老年/男-沧桑 沙哑 岁月沉淀.wav"},
    {"id": "male_righteous", "name": "正直开明男声", "gender": "男", "age": "中年", "scene": "正派/官员", "style": "正直、开明、大气", "path": "不同情绪音色/男-中音、正直、开明.wav"},
    {"id": "male_sarcastic", "name": "阴阳怪气男声", "gender": "男", "age": "青年", "scene": "反派/丑角", "style": "阴阳怪气、讽刺、尖酸", "path": "不同情绪音色/男-中音、阴阳怪气.wav"},
    {"id": "male_cowardly", "name": "胆小窝囊男声", "gender": "男", "age": "青年", "scene": "配角/丑角", "style": "窝囊、胆小、怯懦", "path": "不同情绪音色/男-窝囊 胆小.wav"},
    {"id": "male_insane", "name": "疯癫失常男声", "gender": "男", "age": "中年", "scene": "反派/疯癫", "style": "疯癫、失控、歇斯底里", "path": "不同情绪音色/男-疯癫、失去理智.wav"},
    {"id": "male_arrogant", "name": "傲慢狂妄男声", "gender": "男", "age": "青年", "scene": "反派/天才", "style": "傲慢、狂妄、目空一切", "path": "不同情绪音色/男-傲慢、狂妄.wav"},
    {"id": "male_eunuch", "name": "太监公公声", "gender": "男", "age": "中年", "scene": "宫廷/宦官", "style": "尖细、谄媚、作威作福", "path": "不同年龄人群音色/中年-男声/太监公公.wav"},
    {"id": "male_merchant", "name": "客栈老板男声", "gender": "男", "age": "中年", "scene": "市井/商贩", "style": "热情、圆滑、周到", "path": "不同情绪音色/客栈老板-热情.wav"},
    {"id": "male_teen_boy", "name": "少年男孩", "gender": "男", "age": "少年", "scene": "少年/学童", "style": "明亮、清脆、少年气", "path": "不同年龄人群音色/孩童/男-十二到十四岁的男孩.wav"},
    # 女性
    {"id": "female_strong", "name": "强势强壮女声", "gender": "女", "age": "青年", "scene": "女侠/将军", "style": "强势、强壮、果敢", "path": "不同情绪音色/女-强势、强壮、师姐.wav"},
    {"id": "female_cunning", "name": "古灵精怪女声", "gender": "女", "age": "少女", "scene": "师妹/少女", "style": "古灵精怪、活泼、俏皮", "path": "不同情绪音色/女-古灵精怪、活泼、师姐.wav"},
    {"id": "female_spoiled", "name": "骄纵公主女声", "gender": "女", "age": "少女", "scene": "公主/小姐", "style": "骄纵、甜美、任性", "path": "不同情绪音色/女-骄纵甜美、公主、小姐.wav"},
    {"id": "female_vicious", "name": "恶毒阴狠女声", "gender": "女", "age": "中年", "scene": "反派/妃子", "style": "阴狠、恶毒、算计", "path": "不同年龄人群音色/老年/女-恶毒阴狠.wav"},
    {"id": "female_imperial", "name": "威严太后女声", "gender": "女", "age": "老年", "scene": "太后/至尊", "style": "威严、高傲、不容置疑", "path": "不同年龄人群音色/老年/女-威严、高傲、太后太妃.wav"},
    {"id": "female_coquettish", "name": "娇媚小妾女声", "gender": "女", "age": "青年", "scene": "妾室/风尘", "style": "娇滴滴、妩媚、柔弱", "path": "不同情绪音色/女-小妾、娇滴滴.wav"},
    {"id": "female_optimistic", "name": "乐观开朗女声", "gender": "女", "age": "少女", "scene": "少女/丫鬟", "style": "乐观、开朗、甜美", "path": "不同情绪音色/女-乐观开朗、甜美.wav"},
    {"id": "female_yandere", "name": "病娇反派女声", "gender": "女", "age": "青年", "scene": "反派/病娇", "style": "病娇、偏执、危险", "path": "不同年龄人群音色/热门音色/病娇反派女声.wav"},
    {"id": "female_courtesan", "name": "花魁娘子女声", "gender": "女", "age": "青年", "scene": "花魁/名妓", "style": "妩媚、风情、撩人", "path": "不同年龄人群音色/热门音色/花魁.wav"},
    {"id": "female_stern_elder", "name": "严厉老妇声", "gender": "女", "age": "老年", "scene": "长老/婆婆", "style": "严厉、喑哑、不容情", "path": "不同年龄人群音色/老年/女-低音、喑哑、严肃.wav"},
    {"id": "female_young_girl", "name": "幼小女孩", "gender": "女", "age": "儿童", "scene": "儿童/幼女", "style": "奶声奶气、稚嫩、天真", "path": "不同年龄人群音色/孩童/女-3-5岁的小女孩.wav"},
]


def list_builtin_voices() -> list[dict[str, Any]]:
    voices = []
    for item in CURATED_VOICES:
        path = (PACK_ROOT / item["path"]).resolve()
        voices.append({
            **{k: v for k, v in item.items() if k != "path"},
            "available": path.exists(),
            "audio_url": f"/api/builtin-voices/{item['id']}/audio",
        })
    return voices


def find_voice_path(voice_id: str) -> Path | None:
    shared = (SHARED_VOICES / f"{voice_id}.wav").resolve()
    if shared.exists():
        return shared
    for item in CURATED_VOICES:
        if item["id"] == voice_id:
            path = (PACK_ROOT / item["path"]).resolve()
            try:
                path.relative_to(PACK_ROOT.resolve())
            except ValueError:
                return None
            return path if path.exists() else None
    return None


def compact_voice_catalog() -> list[dict[str, str]]:
    """Full clone voice catalog for LLM prompts (24 voices)."""
    return [
        {k: item[k] for k in ("id", "name", "gender", "age", "scene", "style")}
        for item in CURATED_VOICES
    ]


PRESET_CATALOG = [
    {"id": "冰糖", "name": "冰糖", "gender": "女", "age": "青年", "scene": "通用", "style": "甜美、活泼、明亮"},
    {"id": "茉莉", "name": "茉莉", "gender": "女", "age": "中年", "scene": "通用", "style": "温柔、成熟、知性"},
    {"id": "苏打", "name": "苏打", "gender": "男", "age": "青年", "scene": "通用", "style": "清朗、自然、阳光"},
    {"id": "白桦", "name": "白桦", "gender": "男", "age": "中年", "scene": "通用", "style": "沉稳、磁性、厚重"},
]


def preset_voice_catalog() -> list[dict[str, str]]:
    """Preset-only voice catalog for LLM prompts (4 voices)."""
    return [dict(v) for v in PRESET_CATALOG]


def recommend_voice_id(character: dict[str, Any], narrator: bool = False) -> str:
    """Deterministic fallback when LLM casting is unavailable."""
    if narrator:
        return "narrator_male_literary"

    gender = character.get("gender", "")
    age = character.get("age", "")
    role = character.get("role", "") + character.get("personality", "") + character.get("speaking_style", "")

    if "老" in age:
        return "female_gentle_senior" if "女" in gender else "male_deep_elder"
    if "儿童" in age or "孩" in age:
        return "child_girl_sweet" if "女" in gender else "child_boy"
    if "少年" in age:
        return "female_sweet_girl" if "女" in gender else "male_xiake"
    if any(word in role for word in ["反派", "嚣张", "纨绔", "阴冷", "病娇"]):
        return "female_cold_shijie" if "女" in gender else "male_villain"
    if any(word in role for word in ["师姐", "姐姐", "温柔"]):
        return "female_shijie" if "女" in gender else "male_warm_young"
    if "女" in gender:
        return "female_soft_sister"
    return "male_magnetic"
