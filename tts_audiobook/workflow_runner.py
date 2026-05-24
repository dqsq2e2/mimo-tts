"""One-click audiobook workflow orchestration."""

from __future__ import annotations

import base64
import json
import re
import time
import uuid
from pathlib import Path
from threading import Thread
from typing import Any

from openai import OpenAI

from .audio_merger import merge_wavs, wav_duration_sec
from .character_detector import detect_characters
from .config import CHARACTER_DETECT_MAX_CHARS, MIMO_BASE_URL, MIMO_TOKEN_PLAN_URL, NARRATOR_VOICE
from .cost_tracker import CostTracker
from .llm_config import default_base_url_for_mode, effective_llm_config
from .mimo_client import MiMoTTSClient
from .project_store import STATIC_DIR, clean_filename, load_project, save_chapter, save_project
from .script_parser import parse_script_llm, parse_script_regex
from .voice_catalog import compact_voice_catalog, find_voice_path, recommend_voice_id


TASKS: dict[str, dict[str, Any]] = {}


def start_one_click(project_id: str, mimo_key: str, mode: str, request_cfg: dict[str, Any] | None = None) -> str:
    task_id = uuid.uuid4().hex[:8]
    TASKS[task_id] = {
        "status": "running",
        "stage": "queued",
        "current": 0,
        "total": 0,
        "tokens": 0,
        "cost": 0.0,
        "duration": 0.0,
        "is_free": True,
        "log": [],
        "file": None,
        "files": [],
        "error": None,
        "project_id": project_id,
        "audio_chunks": [],
    }
    llm_cfg = effective_llm_config((request_cfg or {}).get("llm_config", {}))
    options = {
        "force": bool((request_cfg or {}).get("force", False)),
        "extract_characters": (request_cfg or {}).get("extract_characters", True) is not False,
        "cast_voices": (request_cfg or {}).get("cast_voices", True) is not False,
        "use_clone_library": (request_cfg or {}).get("use_clone_library", True) is not False,
        "parse_mode": (request_cfg or {}).get("parse_mode", "llm"),
        "character_scope": (request_cfg or {}).get("character_scope", "selected"),
        "character_batch_chars": int((request_cfg or {}).get("character_batch_chars") or CHARACTER_DETECT_MAX_CHARS),
    }
    project = load_project(project_id)
    if project:
        project["_task_id"] = task_id
        save_project(project_id, project)
    Thread(target=_run_one_click, args=(task_id, project_id, mimo_key, mode, llm_cfg, options), daemon=True).start()
    return task_id


def task_snapshot(task_id: str) -> dict[str, Any]:
    task = TASKS.get(task_id, {})
    keys = ["status", "stage", "current", "total", "tokens", "cost", "duration", "is_free", "log", "file", "files", "error", "project_id", "voice_cast"]
    return {key: task.get(key) for key in keys}


def _run_one_click(task_id: str, project_id: str, mimo_key: str, mode: str, llm_cfg: dict[str, Any], options: dict[str, Any]) -> None:
    task = TASKS[task_id]
    try:
        project = load_project(project_id)
        if not project or not project.get("chapters"):
            raise RuntimeError("请先导入书籍")
        _log(task, "info", f"开始生成：角色提取={'LLM' if options['extract_characters'] else '跳过'}，角色范围={options['character_scope']}，划分={options['parse_mode']}，角色批次={options['character_batch_chars']}字")
        project = _detect_and_parse(task, project_id, project, mimo_key, mode, llm_cfg, options)

        if options["cast_voices"]:
            task["stage"] = "voice_cast"
            chars = project.get("characters", [])
            has_all_builtin = chars and all(c.get("builtin_voice_id") for c in chars) and project.get("narrator_builtin_voice_id")
            if has_all_builtin:
                _log(task, "info", "角色检测阶段已完成克隆音色分配，跳过 voice_cast")
            else:
                _log(task, "info", "LLM 正在从克隆音频库挑选角色音色")
                cast = _cast_voices(project.get("characters", []), project.get("narrator_style", ""), llm_cfg)
                project = _apply_cast(project, cast)
                save_project(project_id, project)
                task["voice_cast"] = cast
                _log(task, "info", f"已分配 {len(cast.get('characters', []))} 个角色音色")

        task["stage"] = "synthesize"
        _synthesize(task_id, project_id, project, mimo_key, mode == "tokenplan", use_clone_library=options["use_clone_library"])
        if task.get("status") == "done":
            task["stage"] = "done"
            _log(task, "success", "一键生成完成")
    except Exception as exc:
        task["status"] = "error"
        task["stage"] = "error"
        task["error"] = str(exc)
        _log(task, "error", str(exc))
    finally:
        project = load_project(project_id)
        if project and project.get("_task_id") == task_id:
            project.pop("_task_id", None)
            save_project(project_id, project)


def _detect_and_parse(task: dict[str, Any], project_id: str, project: dict[str, Any], mimo_key: str, mode: str, llm_cfg: dict[str, Any], options: dict[str, Any]) -> dict[str, Any]:
    if options.get("force"):
        for chapter in project.get("chapters", []):
            chapter.pop("_segments", None)
            chapter.pop("_parsed_done", None)
        project["characters"] = []

    all_chapters = list(enumerate(project.get("chapters", [])))
    selected_chapters = [(index, chapter) for index, chapter in all_chapters if chapter.get("_selected", True) is not False]
    if not selected_chapters:
        raise RuntimeError("没有可处理的章节")
    detect_chapters = all_chapters if options.get("character_scope") == "all" else selected_chapters

    task["stage"] = "detect"
    characters = list(project.get("characters", []))
    total_llm = 0
    batch_limit = max(2000, int(options.get("character_batch_chars") or CHARACTER_DETECT_MAX_CHARS))

    if options.get("extract_characters", True):
        _log(task, "info", f"识别角色与旁白风格（{'整书' if options.get('character_scope') == 'all' else '勾选章节'}，按完整章节打包）")
        batch: list[tuple[int, dict[str, Any]]] = []
        batch_len = 0

        def flush_batch() -> None:
            nonlocal batch, batch_len, total_llm, characters, project
            if not batch:
                return
            titles = "、".join(str(index + 1) for index, _ in batch)
            result = detect_characters(
                "\n\n".join(chapter["text"] for _, chapter in batch),
                api_key=mimo_key,
                use_token_plan=(mode == "tokenplan"),
                llm_config=llm_cfg,
                existing_characters=characters if characters else None,
                use_clone_library=options.get("use_clone_library", True),
            )
            characters = list(result.get("characters", []))
            if result.get("narrator_style"):
                project["narrator_style"] = result["narrator_style"]
            if result.get("narrator_builtin_voice_id"):
                project["narrator_builtin_voice_id"] = result["narrator_builtin_voice_id"]
            total_llm += result.get("_usage", {}).get("total_tokens", 0)
            _log(task, "info", f"章节 {titles} 角色提取完成，累计 {len(characters)} 个角色")
            batch = []
            batch_len = 0

        for chapter_index, chapter in detect_chapters:
            chapter_len = len(chapter.get("text", ""))
            if chapter_len > batch_limit:
                flush_batch()
                _log(task, "info", f"第 {chapter_index + 1} 章 {chapter_len} 字超过角色提取批次上限 {batch_limit}，为避免截断未提交半章；请提高上限或拆章后再提取。")
                continue
            if batch and batch_len + chapter_len > batch_limit:
                flush_batch()
            batch.append((chapter_index, chapter))
            batch_len += chapter_len
        flush_batch()
    else:
        _log(task, "info", "跳过 LLM 角色提取，使用现有/手动角色卡")

    project["characters"] = characters
    save_project(project_id, project)

    task["stage"] = "parse"
    task["total"] = len(selected_chapters)
    task["current"] = 0
    parse_mode = options.get("parse_mode", "llm")
    _log(task, "info", f"划分脚本：{parse_mode}，固定按单章提交；已有分段的章节直接跳过")
    for progress_index, (chapter_index, chapter) in enumerate(selected_chapters):
        if chapter.get("_segments"):
            if not chapter.get("_parsed_done"):
                chapter["_parsed_done"] = True
                save_chapter(project_id, chapter_index, chapter)
            _log(task, "info", f"第 {chapter_index + 1} 章已有分段，跳过划分")
            task["current"] = progress_index + 1
            continue
        segments, used_tokens = _parse_chapter(
            chapter["text"],
            characters,
            mimo_key,
            mode,
            llm_cfg,
            parse_mode=parse_mode,
            task=task,
            chapter_number=chapter_index + 1,
        )
        total_llm += used_tokens
        chapter["_segments"] = segments
        chapter["_parsed_done"] = True
        save_chapter(project_id, chapter_index, chapter)
        task["current"] = progress_index + 1
        _log(task, "info", f"第 {chapter_index + 1} 章脚本已划分")

    project["llm_tokens"] = project.get("llm_tokens", 0) + total_llm
    save_project(project_id, project)
    return load_project(project_id) or project


def _parse_chapter(
    text: str,
    characters: list[dict[str, Any]],
    mimo_key: str,
    mode: str,
    llm_cfg: dict[str, Any],
    parse_mode: str,
    task: dict[str, Any],
    chapter_number: int,
) -> tuple[list[dict[str, Any]], int]:
    if parse_mode == "regex":
        return parse_script_regex(text, characters), 0
    try:
        segments, usage = parse_script_llm(text, characters, api_key=mimo_key, use_token_plan=(mode == "tokenplan"), llm_config=llm_cfg)
        return segments, usage.get("total_tokens", 0)
    except Exception as exc:
        _log(task, "info", f"第 {chapter_number} 章 LLM 划分失败，正则兜底: {exc}")
        return parse_script_regex(text, characters), 0


def _cast_voices(characters: list[dict[str, Any]], narrator_style: str, llm_cfg: dict[str, Any]) -> dict[str, Any]:
    fallback = {
        "narrator_builtin_voice_id": recommend_voice_id({}, narrator=True),
        "characters": [{"name": c.get("name", ""), "builtin_voice_id": recommend_voice_id(c), "reason": "规则兜底匹配"} for c in characters],
    }
    key = llm_cfg.get("key")
    if not key:
        return fallback
    base_url = llm_cfg.get("url") or default_base_url_for_mode("tokenplan")
    model = llm_cfg.get("model") or "deepseek-v4-flash"
    provider = llm_cfg.get("provider") or ("deepseek" if "deepseek" in base_url.lower() else "")
    thinking = llm_cfg.get("thinking") or ("enabled" if provider == "deepseek" else "disabled")
    extra: dict[str, Any] = {"response_format": {"type": "json_object"}, "temperature": 0}
    if thinking == "enabled":
        extra["extra_body"] = {"thinking": {"type": "enabled"}}
        extra["reasoning_effort"] = llm_cfg.get("reasoning_effort") or "high"
        if provider == "deepseek":
            extra.pop("temperature", None)
    elif thinking == "disabled":
        extra["extra_body"] = {"thinking": {"type": "disabled"}}

    payload = {
        "narrator_style": narrator_style,
        "characters": [{k: c.get(k, "") for k in ["name", "gender", "age", "role", "personality", "speaking_style"]} for c in characters],
        "voice_catalog": compact_voice_catalog(),
    }
    system_prompt = (
        "你是有声书配音导演。根据每个角色的性别、年龄、性格、说话风格，"
        "从 voice_catalog 中为每个角色挑选最匹配的 builtin_voice_id。\n\n"
        "【选声优先级】\n"
        "1. 性别必须一致（男角选男声、女角选女声）\n"
        "2. 年龄匹配（儿童→童声、少年→少年音、老年→老年音）\n"
        "3. 风格/场景匹配（scene 和 style 字段尽量贴近角色的 personality 和 speaking_style）\n"
        "4. 旁白根据 narrator_style 挑选（沉稳大气→男声纪录旁白/文学旁白、温柔细腻→磁性女声旁白）\n\n"
        "【铁律】\n"
        "- 每个角色都必须分配 builtin_voice_id，一个都不能漏\n"
        "- builtin_voice_id 必须是 voice_catalog 中存在的 id，禁止编造\n"
        "- 返回纯 JSON，不要 markdown 包裹"
    )
    user_prompt = (
        "为以下角色分配音色，返回 JSON：\n"
        '{"narrator_builtin_voice_id":"旁白音色ID","characters":['
        '{"name":"角色名","builtin_voice_id":"音色ID","reason":"一句话理由"}]}\n\n'
        + json.dumps(payload, ensure_ascii=False)
    )
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
    try:
        response = OpenAI(api_key=key, base_url=base_url).chat.completions.create(model=model, messages=messages, max_completion_tokens=2048, **extra)
        raw = (response.choices[0].message.content or "").strip()
        if not raw and getattr(response.choices[0].message, "reasoning_content", None):
            raw = response.choices[0].message.reasoning_content
        data = _extract_json(raw)
    except Exception as exc:
        print(f"[voice-cast] fallback: {exc}")
        return fallback

    valid = {voice["id"] for voice in compact_voice_catalog()}
    narrator_id = data.get("narrator_builtin_voice_id") if data.get("narrator_builtin_voice_id") in valid else fallback["narrator_builtin_voice_id"]
    by_name = {item.get("name"): item for item in data.get("characters", [])}
    fallback_by_name = {item["name"]: item for item in fallback["characters"]}
    casted = []
    for character in characters:
        name = character.get("name", "")
        item = by_name.get(name, {})
        fb = fallback_by_name.get(name, {"builtin_voice_id": recommend_voice_id(character), "reason": "规则兜底匹配"})
        voice_id = item.get("builtin_voice_id") if item.get("builtin_voice_id") in valid else fb["builtin_voice_id"]
        casted.append({"name": name, "builtin_voice_id": voice_id, "reason": item.get("reason") or fb["reason"]})
    return {"narrator_builtin_voice_id": narrator_id, "characters": casted}


def _apply_cast(project: dict[str, Any], cast: dict[str, Any]) -> dict[str, Any]:
    project["narrator_builtin_voice_id"] = cast.get("narrator_builtin_voice_id") or recommend_voice_id({}, narrator=True)
    cast_map = {item.get("name"): item for item in cast.get("characters", [])}
    for character in project.get("characters", []):
        item = cast_map.get(character.get("name"), {})
        character["builtin_voice_id"] = item.get("builtin_voice_id") or recommend_voice_id(character)
        character["builtin_voice_reason"] = item.get("reason", "")
        character["assigned_voice"] = character.get("assigned_voice") or NARRATOR_VOICE
    return project


def _synthesize(task_id: str, project_id: str, project: dict[str, Any], mimo_key: str, use_token_plan: bool, use_clone_library: bool) -> None:
    task = TASKS[task_id]
    characters = project.get("characters", [])
    narrator_voice = project.get("narrator_voice", NARRATOR_VOICE)
    narrator_style = project.get("narrator_style", "")
    chapters_with_segs = []
    for chapter in project.get("chapters", []):
        if chapter.get("_selected", True) is not False and chapter.get("_segments"):
            chapters_with_segs.append(chapter)
    if not chapters_with_segs:
        raise RuntimeError("没有可合成的脚本分段")

    all_segments = []
    for ch in chapters_with_segs:
        all_segments.extend(ch["_segments"])
    task["total"] = len(all_segments)
    task["current"] = 0
    task["audio_chunks"] = []
    client = MiMoTTSClient(api_key=mimo_key, voice=narrator_voice, style=narrator_style, use_token_plan=use_token_plan)
    clone_client = OpenAI(api_key=mimo_key, base_url=MIMO_TOKEN_PLAN_URL if use_token_plan else MIMO_BASE_URL)
    speaker_voice = {c["name"]: c.get("assigned_voice", narrator_voice) for c in characters}
    speaker_style = {c["name"]: c.get("speaking_style", narrator_style) for c in characters}
    tracker = CostTracker()
    book_dir = STATIC_DIR / clean_filename(project.get("book_title", "audiobook"))
    book_dir.mkdir(exist_ok=True)
    task["files"] = []
    global_index = 0

    for chapter in chapters_with_segs:
        chapter_segments = chapter.get("_segments", [])
        chapter_chunks = []
        for segment in chapter_segments:
            global_index += 1
            started = time.time()
            text = segment["text"].strip()
            if text and text[-1] not in "。！？.!?…~～\"\"」」''":
                text += "。"
            speaker = segment.get("speaker", "旁白")
            voice_sample = _voice_sample_for_segment(project, characters, segment) if use_clone_library else None
            if voice_sample:
                style_hint = speaker_style.get(speaker, narrator_style)
                try:
                    completion = clone_client.chat.completions.create(
                        model="mimo-v2.5-tts-voiceclone",
                        messages=[{"role": "user", "content": style_hint}, {"role": "assistant", "content": text}],
                        audio={"format": "wav", "voice": f"data:audio/wav;base64,{voice_sample}"},
                        timeout=120,
                    )
                    wav = base64.b64decode(completion.choices[0].message.audio.data)
                    usage = {"prompt_tokens": completion.usage.prompt_tokens, "completion_tokens": completion.usage.completion_tokens, "total_tokens": completion.usage.total_tokens}
                except Exception as exc:
                    _log(task, "info", f"克隆音色失败，回退预设音色: {exc}")
                    wav, usage = client.synthesize(text, voice=speaker_voice.get(speaker, narrator_voice), style=speaker_style.get(speaker, narrator_style))
            else:
                wav, usage = client.synthesize(text, voice=speaker_voice.get(speaker, narrator_voice), style=speaker_style.get(speaker, narrator_style))

            elapsed = time.time() - started
            tracker.record(global_index, len(text), usage, wav_duration_sec(wav), elapsed)
            chapter_chunks.append(wav)
            task["current"] = global_index
            task["tokens"] = tracker.total_tokens
            task["cost"] = tracker.total_would_be_cost
            task["duration"] = tracker.total_duration_sec
            _log(task, "info", f"[{global_index}/{task['total']}] {speaker}: {usage['total_tokens']}t {elapsed:.1f}s")

        chapter_title = clean_filename(chapter.get("title", f"第{chapter.get('index', 0) + 1}章"))
        merged = merge_wavs(chapter_chunks)
        filename = f"{clean_filename(project.get('book_title', 'audiobook'))}_{chapter_title}.wav"
        (book_dir / filename).write_bytes(merged)
        task["files"].append(f"{book_dir.name}/{filename}")

    if task["files"]:
        task["file"] = task["files"][0]
    task["status"] = "done"


def _voice_sample_for_segment(project: dict[str, Any], characters: list[dict[str, Any]], segment: dict[str, Any]) -> str | None:
    speaker = segment.get("speaker", "旁白")
    if speaker == "未知":
        speaker = "旁白"
    voice_id = project.get("narrator_builtin_voice_id") if speaker == "旁白" else None
    for character in characters:
        if character.get("name") == speaker:
            voice_id = character.get("builtin_voice_id") or voice_id or recommend_voice_id(character)
            break
    if not voice_id and speaker == "旁白":
        voice_id = recommend_voice_id({}, narrator=True)
    if voice_id:
        path = find_voice_path(voice_id)
        if path:
            return base64.b64encode(path.read_bytes()).decode()
    return None


def _extract_json(raw: str) -> Any:
    raw = (raw or "").strip()
    raw = re.sub(r'^```(?:json|javascript|js)?\s*\n?', "", raw)
    raw = re.sub(r"\n?```\s*$", "", raw).strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\{[\s\S]*\}|\[[\s\S]*\]", raw)
        if match:
            return json.loads(match.group(0))
        raise


def _log(task: dict[str, Any], level: str, message: str) -> None:
    task.setdefault("log", []).append({"level": level, "msg": message})
