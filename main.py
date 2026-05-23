#!/usr/bin/env python3
"""MiMo TTS 有声书制作工具 — 自动角色识别 + 多角色配音 + 成本控制。

用法:
    python main.py novel.txt -o out.wav         # 基础合成
    python main.py novel/ -o out.wav             # 文件夹（每章一个文件）
    python main.py novel.txt --dry-run            # 仅分析角色和费用
    python main.py novel.txt --max-cost 5         # 预算上限 ¥5
    python main.py novel.txt --no-llm-parse       # 零 LLM 成本快速模式

Key 配置（优先级从高到低）:
    1. 命令行 --api-key 参数
    2. MIMO_API_KEY 环境变量
    3. 当前目录的 .env 文件 (MIMO_API_KEY=xxx)
    4. ~/.mimo_key 文件
"""

import argparse
import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tts_audiobook.config import (
    MIMO_API_KEY_ENV,
    MIMO_TOKEN_PLAN_KEY_ENV,
    MIMO_TOKEN_PLAN_URL,
    DEFAULT_CHUNK_SIZE,
    DEFAULT_SILENCE_SEC,
    PRESET_VOICES,
    PRICING_PER_1M,
    PRICING_FLASH_PER_1M,
    NARRATOR_VOICE,
)
from tts_audiobook.text_chunker import chunk_text, detect_chapters, load_text
from tts_audiobook.mimo_client import MiMoTTSClient
from tts_audiobook.audio_merger import merge_wavs, wav_duration_sec, convert_to_mp3
from tts_audiobook.cost_tracker import CostTracker
from tts_audiobook.character_detector import (
    detect_characters, format_character_cards, save_characters, load_characters
)
from tts_audiobook.script_parser import parse_script


def resolve_api_key(cli_key: str, use_token_plan: bool = False) -> str:
    """多来源加载 API Key。"""
    env_var = MIMO_TOKEN_PLAN_KEY_ENV if use_token_plan else MIMO_API_KEY_ENV
    if cli_key:
        return cli_key
    key = os.environ.get(env_var, "")
    if key:
        return key
    for env_path in [Path(".env"), Path(__file__).parent / ".env"]:
        if env_path.exists():
            for line in env_path.read_text(encoding="utf-8").split("\n"):
                if "=" in line and not line.strip().startswith("#"):
                    k, v = line.split("=", 1)
                    if k.strip() == env_var:
                        return v.strip().strip('"').strip("'")
    keyfile = Path.home() / ".mimo_key"
    if keyfile.exists():
        return keyfile.read_text(encoding="utf-8").strip()
    return ""


def main():
    parser = argparse.ArgumentParser(
        description="MiMo TTS 有声书制作工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  %(prog)s novel.txt -o audiobook.wav           # 合成有声书
  %(prog)s novel_chapters/ -o out.wav            # 文件夹（每章一个 .txt）
  %(prog)s novel.txt --dry-run                   # 仅分析角色和预估费用
  %(prog)s novel.txt -o out.wav --max-cost 5     # 预算上限 ¥5
  %(prog)s novel.txt -o out.wav --no-llm-parse   # 快速模式（零 LLM 成本）
  %(prog)s novel.txt -o out.mp3 --format mp3     # 输出 MP3

Key 配置（优先级从高到低）:
  1. --api-key 参数
  2. 环境变量 MIMO_API_KEY
  3. 当前目录 .env 文件: MIMO_API_KEY=xxx
  4. ~/.mimo_key 文件

可用音色: """ + ", ".join(PRESET_VOICES.keys()),
    )

    parser.add_argument("input", help="输入文本文件 (.txt/.md) 或文件夹")
    parser.add_argument("-o", "--output", default="audiobook.wav", help="输出音频文件")
    parser.add_argument("--format", choices=["wav", "mp3"], default="wav")
    parser.add_argument("--voice", default="冰糖", help="旁白/默认音色")
    parser.add_argument("--style", default="", help="朗读风格描述（自然语言）")
    parser.add_argument("--chunk-size", type=int, default=DEFAULT_CHUNK_SIZE)
    parser.add_argument("--silence", type=float, default=DEFAULT_SILENCE_SEC, help="块间停顿秒数")
    parser.add_argument("--max-cost", type=float, default=float("inf"), help="预算上限（元）")
    parser.add_argument("--dry-run", action="store_true", help="仅分析，不合成")
    parser.add_argument("--no-char-detect", action="store_true", help="跳过角色检测")
    parser.add_argument("--no-llm-parse", action="store_true", help="正则解析（零 LLM 成本）")
    parser.add_argument("--per-chapter", action="store_true", help="每章输出独立音频文件")
    parser.add_argument("--api-key", default="", help="MiMo API Key")
    parser.add_argument("--token-plan", action="store_true", help="使用 Token Plan 端点")
    parser.add_argument("--save-chars", default="", help="保存角色卡到 JSON 文件")
    parser.add_argument("--load-chars", default="", help="从 JSON 文件加载角色卡（跳过 LLM 识别）")

    args = parser.parse_args()

    # ── Key 检查 ──
    use_token_plan = args.token_plan
    api_key = resolve_api_key(args.api_key, use_token_plan=use_token_plan)
    if not api_key and not args.dry_run:
        env_name = MIMO_TOKEN_PLAN_KEY_ENV if use_token_plan else MIMO_API_KEY_ENV
        print("=" * 55)
        print(f"  未找到 MiMo API Key! (模式: {'Token Plan' if use_token_plan else '按量付费'})")
        print("")
        print(f"  配置方式: 设置 {env_name} 环境变量或创建 .env 文件")
        print(f"  获取 Key: https://platform.xiaomimimo.com")
        print("=" * 55)
        print("\n提示: 使用 --dry-run 可以在没有 Key 的情况下预览。")
        sys.exit(1)

    if use_token_plan:
        print(f"📡 使用 Token Plan 端点: {MIMO_TOKEN_PLAN_URL}")

    # ── 加载文本 ──
    try:
        full_text = load_text(args.input)
    except FileNotFoundError as e:
        print(f"错误: {e}")
        sys.exit(1)

    if not full_text.strip():
        print("错误: 输入为空")
        sys.exit(1)

    print(f"✓ 已加载文本: {len(full_text)} 字符")

    # ── 章节检测 ──
    chapters = detect_chapters(full_text)
    if len(chapters) > 1:
        print(f"\n📑 检测到 {len(chapters)} 个章节:")
        for i, ch in enumerate(chapters):
            print(f"  [{i+1}] {ch['title']} ({len(ch['content'])} 字符)")
    else:
        print(f"  未检测到章节标题，全文作为整体处理")

    # ── 角色检测 ──
    characters: list[dict] = []
    narrator_voice = args.voice
    narrator_style = args.style
    char_detect_cost = 0.0

    # 优先从缓存文件加载
    if args.load_chars and os.path.exists(args.load_chars):
        print(f"\n📂 从缓存加载角色卡: {args.load_chars}")
        try:
            cached = load_characters(args.load_chars)
            characters = cached.get("characters", [])
            narrator_voice = args.voice or cached.get("narrator_voice", args.voice)
            narrator_style = args.style or cached.get("narrator_style", "")
            if characters:
                print(format_character_cards({"characters": characters, "narrator_voice": narrator_voice, "narrator_style": narrator_style}))
                print("  (角色卡来自缓存，费用: ¥0)")
        except Exception as e:
            print(f"  ⚠ 加载缓存失败: {e}")

    if not characters and not args.no_char_detect and api_key:
        print("\n🔍 正在用 LLM 分析角色...")
        try:
            result = detect_characters(full_text, api_key=api_key, use_token_plan=use_token_plan)
            characters = result.get("characters", [])
            narrator_style = args.style or result.get("narrator_style", "")
            narrator_voice = result.get("narrator_voice", args.voice)
            usage = result.get("_usage", {})

            flash_in = PRICING_FLASH_PER_1M["input"] / 1_000_000
            flash_out = PRICING_FLASH_PER_1M["output"] / 1_000_000
            char_detect_cost = (
                usage.get("prompt_tokens", 0) * flash_in
                + usage.get("completion_tokens", 0) * flash_out
            )

            print(format_character_cards(result))
            print(f"  角色检测费用: ¥{char_detect_cost:.4f} (mimo-v2-flash @ ¥0.07/M)")

            # 保存缓存
            if args.save_chars:
                save_characters(args.save_chars, characters, narrator_voice, narrator_style)
                print(f"  角色卡已保存: {args.save_chars}")
            elif not args.load_chars:
                # 自动保存到默认位置
                default_cache = Path(args.input).stem + "_characters.json"
                save_characters(default_cache, characters, narrator_voice, narrator_style)
                print(f"  角色卡已自动保存: {default_cache}")
        except Exception as e:
            print(f"  ⚠ 角色检测失败: {e}，回退到统一音色")
            characters = []
    elif not characters:
        print("\n⏭ 跳过角色检测，全文用统一音色")

    # ── 确定处理单元：按章节 或 全文 ──
    units: list[dict] = []
    if args.per_chapter and len(chapters) > 1:
        for ch in chapters:
            units.append({"title": ch["title"], "text": ch["content"]})
    else:
        units.append({"title": "有声书", "text": full_text})

    all_outputs: list[str] = []
    grand_tracker = CostTracker(max_cost_yuan=args.max_cost)

    for unit_idx, unit in enumerate(units):
        unit_text = unit["text"]
        unit_title = unit["title"]
        if len(units) > 1:
            print(f"\n{'='*50}\n  处理: {unit_title} ({len(unit_text)} 字符)\n{'='*50}")

        # ── 脚本解析 ──
        if characters:
            print(f"\n📖 正在解析脚本 ({unit_title})...")
            try:
                segments = parse_script(
                    unit_text,
                    characters,
                    narrator_style=narrator_style,
                    narrator_voice=narrator_voice,
                    use_llm=False,  # 默认正则解析，--use-llm-parse 启用 LLM
                    api_key=api_key,
                    use_token_plan=use_token_plan,
                )
                print(f"  解析完成: {len(segments)} 个片段")
                for name in set(seg.get("speaker", "旁白") for seg in segments):
                    count = sum(1 for s in segments if s.get("speaker") == name)
                    print(f"    {name}: {count} 段")
            except Exception as e:
                print(f"  ⚠ 脚本解析失败: {e}，回退到统一音色")
                segments = []
        else:
            segments = []

        if segments:
            chunks = segments
        else:
            raw_chunks = chunk_text(unit_text, args.chunk_size)
            chunks = [
                {"speaker": "旁白", "text": t, "voice": narrator_voice, "style": narrator_style}
                for t in raw_chunks
            ]

        # ── 费用预估 ──
        tracker = CostTracker(max_cost_yuan=args.max_cost)
        est = tracker.estimate([c["text"] for c in chunks])

        print(f"\n📊 ({unit_title}) 费用预估:")
        print(f"  片段数:       {est['chunks']}")
        print(f"  字符数:       {est['total_chars']:,}")
        print(f"  预估 Tokens:  {est['est_tokens']:,}")
        print(f"  预估 TTS:     ¥{est['est_total_cost']:.4f}")
        if PRICING_PER_1M["is_free"]:
            print(f"  ☘ TTS 限时免费，实际: ¥0")

        if args.dry_run:
            continue

        # ── 预算检查 ──
        if args.max_cost != float("inf") and (est["est_total_cost"] + grand_tracker.total_actual_cost) > args.max_cost:
            print(f"\n⚠ 总费用将超预算 ¥{args.max_cost:.2f}，跳过剩余章节。")
            break

        if len(units) == 1:
            input("\n按 Enter 开始合成...")

        # ── 逐段合成 ──
        client = MiMoTTSClient(api_key=api_key, voice=narrator_voice, style=narrator_style, use_token_plan=use_token_plan)
        audio_chunks: list[bytes] = []
        total = len(chunks)

        print(f"\n🎙 合成中 ({total} 个片段)...")
        for i, chunk in enumerate(chunks):
            voice = chunk.get("voice", narrator_voice)
            style = chunk.get("style", narrator_style)
            text = chunk["text"]
            speaker = chunk.get("speaker", "旁白")

            if tracker.would_exceed_budget():
                print(f"\n⛔ 达到预算上限 VND{args.max_cost:.2f}，停止。已完成 {i}/{total}")
                break

            t_start = time.time()
            try:
                wav_data, usage = client.synthesize(text, voice=voice, style=style)
                elapsed = time.time() - t_start
                audio_dur = wav_duration_sec(wav_data)
                tracker.record(i + 1, len(text), usage, audio_dur, elapsed)
                grand_tracker.record(
                    grand_tracker.records and grand_tracker.records[-1].index + 1 or 1,
                    len(text), usage, audio_dur, elapsed
                )
                audio_chunks.append(wav_data)

                print(f"  [{i+1}/{total}] {speaker} | {len(text)}字 | "
                      f"{usage['total_tokens']}t | {elapsed:.1f}s | {tracker.progress_summary(i + 1, total)}")
            except Exception as e:
                print(f"  [{i+1}/{total}] ✗ 失败: {e}")
                if i == 0:
                    print("  首个片段失败，终止。")
                    sys.exit(1)

        if not audio_chunks:
            continue

        # ── 拼接 ──
        print(f"\n🔗 拼接 {len(audio_chunks)} 个片段...")
        merged = merge_wavs(audio_chunks, silence_sec=args.silence)

        # ── 输出 ──
        if args.per_chapter and len(units) > 1:
            safe_name = re.sub(r'[\\/*?:"<>|]', "", unit_title)
            output_path = f"{args.output.rsplit('.', 1)[0]}_{unit_idx+1:02d}_{safe_name}.wav"
        else:
            output_path = args.output

        if args.format == "mp3":
            if not output_path.endswith(".mp3"):
                output_path = output_path.rsplit(".", 1)[0] + ".mp3"
            try:
                merged = convert_to_mp3(merged)
            except ImportError:
                print("⚠ pydub 未安装，输出 WAV")
                output_path = output_path.rsplit(".", 1)[0] + ".wav"

        Path(output_path).write_bytes(merged)
        all_outputs.append(output_path)
        print(f"✓ 已保存: {output_path}")

    # ── 最终报告 ──
    if args.dry_run:
        print(f"\n🔍 预演完成。预估总费用: ¥{est['est_total_cost'] + char_detect_cost:.4f}")
        return

    if all_outputs:
        print(f"\n{'='*50}")
        print(f"  完成! 共生成 {len(all_outputs)} 个文件")
        for p in all_outputs:
            size_mb = os.path.getsize(p) / 1024 / 1024
            print(f"    {p} ({size_mb:.1f} MB)")
        print(grand_tracker.final_report())
        if characters and char_detect_cost > 0:
            print(f"  总计 (含角色检测 ¥{char_detect_cost:.4f}): ¥{grand_tracker.total_actual_cost + char_detect_cost:.4f}")


if __name__ == "__main__":
    main()
