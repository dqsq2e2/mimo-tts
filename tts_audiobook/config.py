"""MiMo TTS 配置常量：模型、音色、定价、风格参考。"""

# API 端点
MIMO_BASE_URL = "https://api.xiaomimimo.com/v1"
MIMO_TOKEN_PLAN_URL = "https://token-plan-cn.xiaomimimo.com/v1"
MIMO_API_KEY_ENV = "MIMO_API_KEY"
MIMO_TOKEN_PLAN_KEY_ENV = "MIMO_TOKEN_PLAN_KEY"

# TTS 模型
MODEL_TTS = "mimo-v2.5-tts"
MODEL_VOICE_DESIGN = "mimo-v2.5-tts-voicedesign"
MODEL_VOICE_CLONE = "mimo-v2.5-tts-voiceclone"

# 音频参数
AUDIO_SAMPLE_RATE = 24000
AUDIO_BIT_DEPTH = 16
AUDIO_CHANNELS = 1
AUDIO_FORMAT = "wav"

# 文本分块
DEFAULT_CHUNK_SIZE = 2000                  # 默认每块字符数
MAX_CHUNK_SIZE = 4000                      # 安全上限（8K tokens 约 5000+ 中文字符）
MIN_CHUNK_SIZE = 50                        # 过短的块合并到前一块
TOKEN_ESTIMATE_PER_CHAR = 1.5              # 中文每字符约 1.5 tokens

# 音频拼接
DEFAULT_SILENCE_SEC = 0.3                  # 块间停顿

# API 重试
MAX_RETRIES = 3
RETRY_BACKOFF_BASE = 2.0                   # 指数退避基数（秒）
REQUEST_TIMEOUT = 120                      # 单次请求超时（秒）

# 角色检测模型（用最便宜的模型做文本分析）
MODEL_CHARACTER_DETECT = "mimo-v2-flash"
MODEL_SCRIPT_PARSE = "mimo-v2-flash"

# 角色检测 LLM 每次最多分析的文本长度
CHARACTER_DETECT_MAX_CHARS = 120000  # mimo-v2-flash 上下文 256K，120K 字 ≈ 180K tokens，安全

# 音色自动分配规则：性别 + 年龄段 → 预置音色
VOICE_ASSIGN_RULES: dict[str, dict[str, str]] = {
    "男": {
        "青年": "苏打",
        "中年": "白桦",
        "老年": "白桦",
        "少年": "苏打",
        "儿童": "苏打",
        "默认": "苏打",
    },
    "女": {
        "青年": "冰糖",
        "中年": "茉莉",
        "老年": "茉莉",
        "少年": "冰糖",
        "儿童": "冰糖",
        "默认": "冰糖",
    },
}

# 旁白音色
NARRATOR_VOICE = "茉莉"

# 预置音色列表
PRESET_VOICES: dict[str, dict[str, str]] = {
    "冰糖":  {"voice_id": "冰糖", "language": "中文", "gender": "女"},
    "茉莉":  {"voice_id": "茉莉", "language": "中文", "gender": "女"},
    "苏打":  {"voice_id": "苏打", "language": "中文", "gender": "男"},
    "白桦":  {"voice_id": "白桦", "language": "中文", "gender": "男"},
    "Mia":   {"voice_id": "Mia",   "language": "英文", "gender": "女"},
    "Chloe": {"voice_id": "Chloe", "language": "英文", "gender": "女"},
    "Milo":  {"voice_id": "Milo",  "language": "英文", "gender": "男"},
    "Dean":  {"voice_id": "Dean",  "language": "英文", "gender": "男"},
}

# 风格标签参考（用于 --style-hint 提示）
STYLE_TAGS = {
    "情绪": ["开心", "悲伤", "愤怒", "恐惧", "惊讶", "兴奋", "委屈", "平静", "怅然", "欣慰", "无奈", "愧疚"],
    "语调": ["温柔", "高冷", "活泼", "严肃", "慵懒", "俏皮", "深沉", "干练", "凌厉"],
    "音色": ["磁性", "醇厚", "清亮", "空灵", "稚嫩", "苍老", "甜美", "沙哑"],
    "腔调": ["夹子音", "御姐音", "正太音", "大叔音", "台湾腔"],
    "方言": ["东北话", "四川话", "河南话", "粤语"],
    "角色": ["孙悟空", "林黛玉"],
}

# 定价（国内，¥ / 1M tokens）— TTS 系列目前限时免费
# 保留定价表以备付费后使用
PRICING_PER_1M = {
    "input":  1.40,    # mimo-v2.5-tts 输入单价
    "output": 7.00,    # mimo-v2.5-tts 输出单价
    "is_free": True,   # 当前限时免费
}

# mimo-v2-flash 定价（用于角色检测，极便宜）
PRICING_FLASH_PER_1M = {
    "input": 0.07,
    "output": 0.70,
}

# 联网插件定价
WEB_SEARCH_PRICE_PER_1K = 25.0             # ¥ / 1000 次

# 章节检测正则
CHAPTER_PATTERNS = [
    r"^第[零一二三四五六七八九十百千\d]+章\s*[^\n]*",     # 第一章 xxx
    r"^第[零一二三四五六七八九十百千\d]+节\s*[^\n]*",     # 第一节 xxx
    r"^Chapter\s+\d+[^\n]*",                            # Chapter 1 xxx
    r"^CH\.\s*\d+[^\n]*",                               # CH. 1 xxx
    r"^[第]\s*\d+\s*[章节][^\n]*",                       # 第1章 / 第1节
    r"^Part\s+\d+[^\n]*",                               # Part 1 xxx
    r"^#{1,3}\s*(?:第[零一二三四五六七八九十百千\d]+章|Chapter)",  # Markdown 标题
]

# 直接接受的文件格式
SUPPORTED_INPUT_FORMATS = [".txt", ".md"]

# 直接接受目录（每章一个文件）
CHAPTER_FILE_PATTERN = r".*[第章][零一二三四五六七八九十百千\d]+.*\.(txt|md)$"
