"""Project persistence helpers for audiobook generation."""

from __future__ import annotations

import json
import re
import shutil
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import NARRATOR_VOICE
from .text_chunker import detect_chapters


ROOT_DIR = Path(__file__).resolve().parent.parent
PROJECTS_DIR = ROOT_DIR / "projects"
STATIC_DIR = ROOT_DIR / "static"
PROJECTS_DIR.mkdir(exist_ok=True)
STATIC_DIR.mkdir(exist_ok=True)


def project_dir(project_id: str) -> Path:
    return PROJECTS_DIR / project_id


def project_file(project_id: str) -> Path:
    return project_dir(project_id) / "project.json"


def chapters_dir(project_id: str) -> Path:
    return project_dir(project_id) / "chapters"


def clean_filename(value: str, fallback: str = "audiobook") -> str:
    safe = re.sub(r'[\\/*?:"<>|]', "", value or "").strip()
    return safe[:80] or fallback


def list_projects() -> list[dict[str, Any]]:
    projects = []
    for folder in sorted(PROJECTS_DIR.iterdir(), key=lambda x: x.name, reverse=True):
        pf = folder / "project.json"
        if folder.is_dir() and pf.exists():
            data = json.loads(pf.read_text(encoding="utf-8"))
            data["id"] = folder.name
            projects.append(data)
    return projects


def create_project(book_title: str = "未命名") -> dict[str, Any]:
    project_id = uuid.uuid4().hex[:8]
    project = {
        "id": project_id,
        "book_title": book_title or "未命名",
        "chapters": [],
        "chapter_count": 0,
        "total_chars": 0,
        "narrator_voice": NARRATOR_VOICE,
        "narrator_style": "",
        "characters": [],
        "llm_tokens": 0,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "updated_at": "",
    }
    save_project(project_id, project)
    return project


def save_project(project_id: str, data: dict[str, Any]) -> None:
    folder = project_dir(project_id)
    folder.mkdir(parents=True, exist_ok=True)
    data = dict(data)
    data.pop("id", None)
    chapters = data.pop("chapters", [])
    data["updated_at"] = datetime.now().isoformat(timespec="seconds")
    data["chapter_count"] = len(chapters)
    data["total_chars"] = sum(ch.get("chars", len(ch.get("text", ""))) for ch in chapters)
    project_file(project_id).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    cd = chapters_dir(project_id)
    cd.mkdir(exist_ok=True)
    for old in cd.glob("*.json"):
        if old.stem.isdigit() and int(old.stem) >= len(chapters):
            old.unlink()
    for index, chapter in enumerate(chapters):
        (cd / f"{index}.json").write_text(json.dumps(chapter, ensure_ascii=False, indent=2), encoding="utf-8")


def save_chapter(project_id: str, index: int, chapter: dict[str, Any]) -> None:
    cd = chapters_dir(project_id)
    cd.mkdir(exist_ok=True)
    (cd / f"{index}.json").write_text(json.dumps(chapter, ensure_ascii=False, indent=2), encoding="utf-8")


def load_project(project_id: str, load_chapters: bool = True) -> dict[str, Any] | None:
    pf = project_file(project_id)
    if not pf.exists():
        return None
    project = {"id": project_id, **json.loads(pf.read_text(encoding="utf-8"))}
    if load_chapters:
        chapters = []
        cd = chapters_dir(project_id)
        if cd.exists():
            for item in sorted(cd.iterdir(), key=lambda x: int(x.stem) if x.stem.isdigit() else 0):
                if item.suffix == ".json":
                    try:
                        chapters.append(json.loads(item.read_text(encoding="utf-8")))
                    except (json.JSONDecodeError, OSError):
                        pass
        project["chapters"] = chapters
    return project


def import_book(project_id: str, text: str) -> dict[str, Any]:
    project = load_project(project_id)
    if not project:
        raise FileNotFoundError("project not found")
    chapters = detect_chapters(text)
    if not chapters or len(chapters) <= 1:
        title = chapters[0]["title"] if chapters else "全文"
        project["chapters"] = [{"title": title, "text": text.strip(), "chars": len(text), "_selected": True, "imported_at": datetime.now().isoformat(timespec="seconds")}]
    else:
        project["chapters"] = [
            {"title": ch["title"], "text": ch["content"], "chars": len(ch["content"]), "_selected": True, "imported_at": datetime.now().isoformat(timespec="seconds")}
            for ch in chapters
        ]
    save_project(project_id, project)
    return load_project(project_id) or project


def create_project_from_text(book_title: str, text: str) -> dict[str, Any]:
    project = create_project(book_title)
    return import_book(project["id"], text)


def delete_project(project_id: str) -> None:
    project = load_project(project_id, load_chapters=False)
    if project:
        audio_dir = STATIC_DIR / clean_filename(project.get("book_title", ""))
        if audio_dir.exists():
            shutil.rmtree(audio_dir)
    folder = project_dir(project_id)
    if folder.exists():
        shutil.rmtree(folder)
