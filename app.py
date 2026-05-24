#!/usr/bin/env python3
"""Clean Flask API entrypoint for the Vue audiobook studio."""

from __future__ import annotations

import os
import re
from pathlib import Path

from flask import Flask, jsonify, request, send_file, send_from_directory

from tts_audiobook.config import MIMO_API_KEY_ENV, MIMO_TOKEN_PLAN_KEY_ENV
from tts_audiobook.llm_config import (
    clear_llm_config,
    default_base_url_for_mode,
    probe_json_mode,
    read_llm_config,
    save_llm_config,
)
from tts_audiobook.project_store import (
    STATIC_DIR,
    create_project,
    create_project_from_text,
    delete_project,
    import_book,
    list_projects,
    load_project,
    save_chapter,
    save_project,
)
from tts_audiobook.voice_catalog import find_voice_path, list_builtin_voices
from tts_audiobook.workflow_runner import start_one_click, task_snapshot


ROOT = Path(__file__).resolve().parent
FRONTEND_DIST = ROOT / "frontend" / "dist"
RUNTIME_DIR = ROOT / ".runtime"
RUNTIME_KEYS_FILE = RUNTIME_DIR / "api_keys.env"

app = Flask(__name__)


def load_api_key(mode: str = "tokenplan") -> str:
    env_var = MIMO_TOKEN_PLAN_KEY_ENV if mode == "tokenplan" else MIMO_API_KEY_ENV
    key = os.environ.get(env_var, "")
    if key:
        return key
    for env_file in (RUNTIME_KEYS_FILE, ROOT / ".env"):
        if not env_file.exists():
            continue
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.strip().startswith("#"):
                name, value = line.split("=", 1)
                if name.strip() == env_var:
                    return value.strip().strip('"').strip("'")
    legacy_key = Path.home() / ".mimo_key"
    return legacy_key.read_text(encoding="utf-8").strip() if legacy_key.exists() else ""


def write_api_key(mode: str, key: str) -> None:
    env_var = MIMO_TOKEN_PLAN_KEY_ENV if mode == "tokenplan" else MIMO_API_KEY_ENV
    os.environ[env_var] = key
    RUNTIME_DIR.mkdir(exist_ok=True)
    env_file = RUNTIME_KEYS_FILE
    existing = env_file.read_text(encoding="utf-8") if env_file.exists() else ""
    if re.search(rf"^{env_var}=", existing, flags=re.MULTILINE):
        existing = re.sub(rf"^{env_var}=.*$", f"{env_var}={key}", existing, flags=re.MULTILINE)
    else:
        existing = existing.rstrip() + f"\n{env_var}={key}\n"
    env_file.write_text(existing, encoding="utf-8")


def mask_secret(secret: str) -> str:
    return "" if not secret else f"{secret[:3]}***{secret[-4:]}" if len(secret) > 8 else "***"


@app.get("/")
def index():
    if FRONTEND_DIST.exists():
        return send_from_directory(FRONTEND_DIST, "index.html")
    return jsonify({"ok": True, "message": "Run `npm install && npm run build` in frontend/ or use `npm run dev`."})


@app.get("/assets/<path:path>")
def frontend_assets(path):
    return send_from_directory(FRONTEND_DIST / "assets", path)


@app.get("/api/key-status")
def key_status():
    mode = request.args.get("mode", "tokenplan")
    key = load_api_key(mode)
    return jsonify({"has_key": bool(key), "key_masked": mask_secret(key), "mode": mode})


@app.post("/api/set-key")
def set_key():
    data = request.get_json(silent=True) or {}
    key = (data.get("key") or "").strip()
    mode = data.get("mode", "tokenplan")
    if not key:
        return jsonify({"error": "missing key"}), 400
    write_api_key(mode, key)
    return jsonify({"ok": True})


@app.route("/api/llm-config", methods=["GET", "POST", "DELETE"])
def llm_config_api():
    if request.method == "GET":
        return jsonify(read_llm_config())
    if request.method == "DELETE":
        clear_llm_config()
        return jsonify({"ok": True})
    return jsonify({"ok": True, **save_llm_config(request.get_json(silent=True) or {})})


@app.post("/api/llm-json-probe")
def llm_json_probe():
    data = request.get_json(silent=True) or {}
    provider = data.get("provider", "")
    mode = data.get("mode") or ("tokenplan" if provider.lower() == "mimo" else "normal")
    saved = read_llm_config(include_key=True)
    key = (data.get("key") or "").strip() or saved.get("key") or load_api_key(mode)
    base_url = (data.get("url") or "").strip() or saved.get("url") or default_base_url_for_mode(mode)
    model = (data.get("model") or "").strip() or saved.get("model") or ("mimo-v2.5" if mode == "tokenplan" else "deepseek-v4-flash")
    thinking = (data.get("thinking") or saved.get("thinking") or ("enabled" if provider.lower() == "deepseek" else "disabled")).strip()
    if not key:
        return jsonify({"error": "missing api key"}), 400
    try:
        return jsonify(probe_json_mode(key, base_url, model, thinking, provider=provider))
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.get("/api/workflow")
def workflow():
    return jsonify({
        "steps": [
            {"id": "import", "title": "导入", "detail": "粘贴或上传书籍，自动分章。"},
            {"id": "analyze", "title": "理解", "detail": "LLM 识别角色、别名、旁白风格并划分脚本。"},
            {"id": "cast", "title": "选声", "detail": "LLM 从克隆音频库中为角色挑选音色。"},
            {"id": "synth", "title": "合成", "detail": "按角色音色合成并输出有声书音频。"},
        ],
        "defaults": {"llm_provider": "deepseek", "thinking": "enabled", "mimo_mode": "tokenplan"},
    })


@app.get("/api/builtin-voices")
def builtin_voices():
    return jsonify({"voices": list_builtin_voices()})


@app.get("/api/builtin-voices/<voice_id>/audio")
def builtin_voice_audio(voice_id):
    path = find_voice_path(voice_id)
    if not path:
        return jsonify({"error": "voice not found"}), 404
    return send_file(path)


@app.get("/api/projects")
def projects_list():
    return jsonify(list_projects())


@app.post("/api/projects")
def projects_create():
    data = request.get_json(silent=True) or {}
    return jsonify(create_project(data.get("book_title", "未命名")))


@app.get("/api/projects/<project_id>")
def projects_get(project_id):
    project = load_project(project_id)
    return jsonify(project) if project else (jsonify({"error": "not found"}), 404)


@app.delete("/api/projects/<project_id>")
def projects_delete(project_id):
    delete_project(project_id)
    return jsonify({"ok": True})


@app.post("/api/projects/<project_id>/import-book")
def projects_import_book(project_id):
    data = request.get_json(silent=True) or {}
    text = data.get("text", "")
    if not text.strip():
        return jsonify({"error": "无文本"}), 400
    try:
        return jsonify(import_book(project_id, text))
    except FileNotFoundError:
        return jsonify({"error": "not found"}), 404


@app.put("/api/projects/<project_id>/characters")
def project_save_characters(project_id):
    project = load_project(project_id)
    if not project:
        return jsonify({"error": "not found"}), 404
    data = request.get_json(silent=True) or {}
    project["characters"] = data.get("characters", [])
    project["narrator_voice"] = data.get("narrator_voice", project.get("narrator_voice", "茉莉"))
    project["narrator_style"] = data.get("narrator_style", project.get("narrator_style", ""))
    project["narrator_builtin_voice_id"] = data.get("narrator_builtin_voice_id", project.get("narrator_builtin_voice_id", ""))
    save_project(project_id, project)
    return jsonify(load_project(project_id))


@app.get("/api/projects/<project_id>/chapters")
def project_chapters(project_id):
    project = load_project(project_id)
    if not project:
        return jsonify({"error": "not found"}), 404
    chapters = [
        {
            "index": index,
            "title": chapter.get("title", f"第{index + 1}章"),
            "chars": chapter.get("chars", len(chapter.get("text", ""))),
            "selected": chapter.get("_selected", True) is not False,
            "parsed": bool(chapter.get("_segments")),
        }
        for index, chapter in enumerate(project.get("chapters", []))
    ]
    return jsonify({"chapters": chapters})


@app.put("/api/projects/<project_id>/chapters/<int:index>")
def project_save_chapter(project_id, index):
    project = load_project(project_id)
    if not project:
        return jsonify({"error": "not found"}), 404
    chapters = project.get("chapters", [])
    if index < 0 or index >= len(chapters):
        return jsonify({"error": "invalid chapter"}), 400
    data = request.get_json(silent=True) or {}
    chapter = chapters[index]
    if "title" in data:
        chapter["title"] = data["title"]
    if "text" in data:
        chapter["text"] = data["text"]
        chapter["chars"] = len(data["text"])
        chapter.pop("_segments", None)
        chapter.pop("_parsed_done", None)
    if "selected" in data:
        chapter["_selected"] = bool(data["selected"])
    save_chapter(project_id, index, chapter)
    return jsonify({"ok": True, "chapter": chapter})


@app.delete("/api/projects/<project_id>/chapters/<int:index>")
def project_delete_chapter(project_id, index):
    project = load_project(project_id)
    if not project:
        return jsonify({"error": "not found"}), 404
    chapters = project.get("chapters", [])
    if index < 0 or index >= len(chapters):
        return jsonify({"error": "invalid chapter"}), 400
    chapters.pop(index)
    project["chapters"] = chapters
    save_project(project_id, project)
    return jsonify(load_project(project_id))


@app.put("/api/projects/<project_id>/chapters/bulk-select")
def project_bulk_select_chapters(project_id):
    """Batch update selected state for all chapters in one request."""
    data = request.get_json(silent=True) or {}
    selected_state = bool(data.get("selected", False))
    project = load_project(project_id)
    if not project:
        return jsonify({"error": "not found"}), 404
    for chapter in project.get("chapters", []):
        chapter["_selected"] = selected_state
    save_project(project_id, project)
    return jsonify(load_project(project_id))


@app.get("/api/projects/<project_id>/chapters/<int:index>/segments")
def project_get_segments(project_id, index):
    project = load_project(project_id)
    if not project:
        return jsonify({"error": "not found"}), 404
    chapters = project.get("chapters", [])
    if index < 0 or index >= len(chapters):
        return jsonify({"error": "invalid chapter"}), 400
    chapter = chapters[index]
    return jsonify({
        "title": chapter.get("title", f"第{index + 1}章"),
        "text": chapter.get("text", ""),
        "segments": chapter.get("_segments", []),
    })


@app.put("/api/projects/<project_id>/chapters/<int:index>/segments")
def project_save_segments(project_id, index):
    project = load_project(project_id)
    if not project:
        return jsonify({"error": "not found"}), 404
    chapters = project.get("chapters", [])
    if index < 0 or index >= len(chapters):
        return jsonify({"error": "invalid chapter"}), 400
    data = request.get_json(silent=True) or {}
    chapter = chapters[index]
    chapter["_segments"] = data.get("segments", [])
    chapter["_parsed_done"] = True
    save_chapter(project_id, index, chapter)
    return jsonify({"ok": True})


@app.post("/api/one-click-book")
def one_click_book():
    data = request.get_json(silent=True) or {}
    text = data.get("text", "")
    if not text.strip():
        return jsonify({"error": "请先导入书籍正文"}), 400
    mode = data.get("mode", "tokenplan")
    mimo_key = load_api_key(mode)
    if not mimo_key:
        return jsonify({"error": f"请先配置 MiMo {mode} API Key"}), 400
    project = create_project_from_text(data.get("book_title", "未命名"), text)
    task_id = start_one_click(project["id"], mimo_key, mode, data)
    return jsonify({"project_id": project["id"], "task_id": task_id})


@app.post("/api/projects/<project_id>/one-click-generate")
def project_one_click(project_id):
    data = request.get_json(silent=True) or {}
    mode = data.get("mode", "tokenplan")
    mimo_key = load_api_key(mode)
    if not mimo_key:
        return jsonify({"error": f"请先配置 MiMo {mode} API Key"}), 400
    if not load_project(project_id):
        return jsonify({"error": "not found"}), 404
    task_id = start_one_click(project_id, mimo_key, mode, data)
    return jsonify({"project_id": project_id, "task_id": task_id})


@app.post("/api/projects/<project_id>/detect-characters")
def project_detect_characters(project_id):
    """Run LLM character detection on selected chapters with existing characters as context."""
    data = request.get_json(silent=True) or {}
    mode = data.get("mode", "tokenplan")
    mimo_key = load_api_key(mode)
    if not mimo_key:
        return jsonify({"error": f"请先配置 MiMo {mode} API Key"}), 400
    project = load_project(project_id)
    if not project:
        return jsonify({"error": "not found"}), 404

    from tts_audiobook.llm_config import effective_llm_config
    from tts_audiobook.character_detector import detect_characters

    llm_cfg = effective_llm_config(data.get("llm_config", {}))
    use_clone = data.get("use_clone_library", True) is not False
    batch_limit = max(2000, int(data.get("character_batch_chars") or 120000))

    all_chapters = list(enumerate(project.get("chapters", [])))
    selected = [(i, ch) for i, ch in all_chapters if ch.get("_selected", True) is not False]
    if not selected:
        return jsonify({"error": "没有选中的章节"}), 400

    existing = list(project.get("characters", []))
    characters = list(existing)
    for chapter_index, chapter in selected:
        chapter_len = len(chapter.get("text", ""))
        if chapter_len <= batch_limit:
            result = detect_characters(
                chapter["text"], api_key=mimo_key, use_token_plan=(mode == "tokenplan"),
                llm_config=llm_cfg, existing_characters=characters if characters else None,
                use_clone_library=use_clone,
            )
            characters = list(result.get("characters", []))
            if result.get("narrator_style"):
                project["narrator_style"] = result["narrator_style"]
            if result.get("narrator_builtin_voice_id"):
                project["narrator_builtin_voice_id"] = result["narrator_builtin_voice_id"]

    project["characters"] = characters
    save_project(project_id, project)
    return jsonify(load_project(project_id))


@app.post("/api/projects/<project_id>/chapters/<int:index>/reparse")
def project_reparse_chapter(project_id, index):
    """Re-parse a chapter with LLM, overwriting existing segments."""
    data = request.get_json(silent=True) or {}
    mode = data.get("mode", "tokenplan")
    mimo_key = load_api_key(mode)
    if not mimo_key:
        return jsonify({"error": f"请先配置 MiMo {mode} API Key"}), 400
    project = load_project(project_id)
    if not project:
        return jsonify({"error": "not found"}), 404
    chapters = project.get("chapters", [])
    if index < 0 or index >= len(chapters):
        return jsonify({"error": "invalid chapter"}), 400

    from tts_audiobook.llm_config import effective_llm_config
    from tts_audiobook.script_parser import parse_script_llm, parse_script_regex

    llm_cfg = effective_llm_config(data.get("llm_config", {}))
    chapter = chapters[index]
    characters = project.get("characters", [])

    try:
        segments, usage = parse_script_llm(chapter["text"], characters, api_key=mimo_key,
                                           use_token_plan=(mode == "tokenplan"), llm_config=llm_cfg)
    except Exception as exc:
        segments = parse_script_regex(chapter["text"], characters)

    chapter["_segments"] = segments
    chapter["_parsed_done"] = True
    save_chapter(project_id, index, chapter)
    return jsonify({"title": chapter.get("title", f"第{index + 1}章"), "text": chapter.get("text", ""), "segments": segments})


@app.get("/api/task-progress/<task_id>")
def task_progress(task_id):
    return jsonify(task_snapshot(task_id))


@app.get("/api/download/<path:filename>")
def download(filename):
    path = STATIC_DIR / filename
    return send_file(path, as_attachment=True, download_name=path.name) if path.exists() else ("Not found", 404)


if __name__ == "__main__":
    host = os.environ.get("FLASK_HOST", "0.0.0.0")
    port = int(os.environ.get("FLASK_PORT", "5000"))
    debug = os.environ.get("FLASK_DEBUG", "0") == "1"
    app.run(host=host, port=port, debug=debug)
