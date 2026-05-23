#!/usr/bin/env python3
"""MiMo TTS 有声书 — Web 界面 (项目制 / 分章节 / 角色卡持久化)。

启动: python app.py
访问: http://localhost:5000
"""

import base64, json, os, re, sys, time, uuid, shutil
from datetime import datetime
from pathlib import Path
from threading import Thread

from flask import Flask, render_template_string, request, jsonify, send_file

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tts_audiobook.config import (
    MIMO_BASE_URL, MIMO_TOKEN_PLAN_URL,
    MIMO_API_KEY_ENV, MIMO_TOKEN_PLAN_KEY_ENV,
    MODEL_TTS, NARRATOR_VOICE, PRESET_VOICES, PRICING_PER_1M,
)
from tts_audiobook.mimo_client import MiMoTTSClient
from tts_audiobook.audio_merger import merge_wavs, wav_duration_sec
from tts_audiobook.cost_tracker import CostTracker
from tts_audiobook.character_detector import detect_characters, detect_and_parse
from tts_audiobook.script_parser import parse_script_regex
from tts_audiobook.script_parser import parse_script
from openai import OpenAI
from tts_audiobook.text_chunker import detect_chapters as detect_chapter_headers

app = Flask(__name__)
PROJECTS_DIR = Path(__file__).parent / "projects"
PROJECTS_DIR.mkdir(exist_ok=True)
STATIC_DIR = Path(__file__).parent / "static"
STATIC_DIR.mkdir(exist_ok=True)
_tasks: dict[str, dict] = {}

# ── Key ──
def _load_api_key(mode: str = "normal") -> str:
    env_var = MIMO_TOKEN_PLAN_KEY_ENV if mode == "tokenplan" else MIMO_API_KEY_ENV
    key = os.environ.get(env_var, "")
    if key: return key
    for p in [Path(".env"), Path(__file__).parent / ".env"]:
        if p.exists():
            content = p.read_text(encoding="utf-8")
            for line in content.split("\n"):
                if "=" in line and not line.strip().startswith("#"):
                    k, v = line.split("=", 1)
                    if k.strip() == env_var: return v.strip().strip('"').strip("'")
    kf = Path.home() / ".mimo_key"
    if kf.exists(): return kf.read_text(encoding="utf-8").strip()
    return ""

# ── 项目文件操作 ──
def _proj_dir(pid): return PROJECTS_DIR / pid
def _proj_file(pid): return _proj_dir(pid) / "project.json"
def _chapters_dir(pid): return _proj_dir(pid) / "chapters"

def _list_projects():
    projects = []
    for d in sorted(PROJECTS_DIR.iterdir(), key=lambda x: x.name, reverse=True):
        pf = d / "project.json"
        if d.is_dir() and pf.exists():
            data = json.loads(pf.read_text(encoding="utf-8"))
            data["id"] = d.name
            # 列表中不加载章节全文，只统计
            if "chapters" in data:
                data["chapter_count"] = len(data["chapters"])
            projects.append(data)
    return projects

def _save_project(pid, data):
    d = _proj_dir(pid); d.mkdir(parents=True, exist_ok=True)
    data["updated_at"] = datetime.now().isoformat(timespec="seconds")
    # 分离章节：保存到独立文件
    chs = data.pop("chapters", [])
    data["chapter_count"] = len(chs)
    data["total_chars"] = sum(c.get("chars", 0) for c in chs)
    _proj_file(pid).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    # 保存每章到独立 JSON
    cd = _chapters_dir(pid); cd.mkdir(exist_ok=True)
    for i, ch in enumerate(chs):
        (cd / f"{i}.json").write_text(json.dumps(ch, ensure_ascii=False, indent=2), encoding="utf-8")
    data["chapters"] = chs  # 恢复内存中的数据

def _load_project(pid, load_chapters=True):
    pf = _proj_file(pid)
    if not pf.exists(): return None
    data = json.loads(pf.read_text(encoding="utf-8"))
    proj = {"id": pid, **data}
    # 加载章节
    if load_chapters:
        cd = _chapters_dir(pid)
        chapters = []
        if cd.exists():
            for f in sorted(cd.iterdir(), key=lambda x: int(x.stem) if x.stem.isdigit() else 0):
                if f.suffix == ".json":
                    chapters.append(json.loads(f.read_text(encoding="utf-8")))
        proj["chapters"] = chapters
    return proj

def _save_chapter(pid, idx, chapter):
    cd = _chapters_dir(pid); cd.mkdir(exist_ok=True)
    (cd / f"{idx}.json").write_text(json.dumps(chapter, ensure_ascii=False, indent=2), encoding="utf-8")

HTML = r"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>MiMo TTS — 有声书制作</title>
<link rel="icon" href="https://mimo.mi.com/favicon.f9495d77.png">
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;background:#0f0f1a;color:#e0e0e0;min-height:100vh}
.header{background:#1a1a2e;padding:12px 24px;display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid #2a2a4a}
.header h1{font-size:17px;background:linear-gradient(135deg,#ff6b6b,#ffa500);-webkit-background-clip:text;-webkit-text-fill-color:transparent}
.container{max-width:960px;margin:0 auto;padding:18px}
.card{background:#1a1a2e;border-radius:10px;padding:18px;margin-bottom:12px;border:1px solid #2a2a4a}
.card h2{font-size:14px;margin-bottom:12px;color:#ffa500}
.card h3{font-size:13px;color:#ccc;margin:10px 0 6px}
label{display:block;font-size:11px;color:#aaa;margin-bottom:3px}
input,textarea,select{width:100%;padding:7px 10px;border-radius:6px;border:1px solid #333;background:#111;color:#eee;font-size:12px;font-family:inherit}
textarea{resize:vertical;min-height:80px}
.btn{padding:7px 14px;border-radius:6px;border:none;font-size:12px;cursor:pointer;font-weight:500;transition:all .15s}
.btn-p{background:linear-gradient(135deg,#ff6b6b,#ff8c00);color:#fff}.btn-p:hover{opacity:.9}
.btn-s{background:#2a2a4a;color:#ccc;border:1px solid #444}.btn-s:hover{background:#333}
.btn-d{background:#ff6b6b22;color:#ff6b6b;border:1px solid #ff6b6b44}
.btn-g{background:#51cf6622;color:#51cf66;border:1px solid #51cf6644}
.btn:disabled{opacity:.4;cursor:not-allowed}
.btn-sm{padding:3px 8px;font-size:10px}
.btn-xs{padding:2px 6px;font-size:10px}
.btn-row{display:flex;gap:6px;margin-top:10px;flex-wrap:wrap;align-items:center}
.row{display:flex;gap:10px;align-items:center;flex-wrap:wrap}
.grow{flex:1}
.page{display:none}.page.active{display:block}
.proj-list{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:10px}
.proj-card{background:#111;border:1px solid #2a2a4a;border-radius:8px;padding:14px;cursor:pointer;transition:all .15s}
.proj-card:hover{border-color:#ffa500}
.proj-card h3{font-size:14px;margin-bottom:4px}
.proj-card .meta{font-size:10px;color:#666}.
.proj-card .chars{font-size:10px;color:#888;margin-top:4px}
.chapter-item{display:flex;align-items:center;justify-content:space-between;padding:8px 10px;border-bottom:1px solid #1a1a1a;font-size:12px;gap:8px}
.chapter-item:hover{background:#111}
.chapter-item .idx{color:#666;width:24px;flex-shrink:0}
.chapter-item .title{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.chapter-item .chars{color:#666;font-size:10px;flex-shrink:0}
.char-card{border:1px solid #2a2a4a;border-radius:8px;padding:12px;margin-bottom:8px;background:#0f0f1a}
.char-card .head{display:flex;align-items:center;gap:10px;margin-bottom:8px}
.char-card .avatar{width:34px;height:34px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-weight:700;font-size:14px;flex-shrink:0}
.char-card .avatar.f{background:#ff6b6b33;color:#ff6b6b}
.char-card .avatar.m{background:#4dabf733;color:#4dabf7}
.char-card .info{flex:1;min-width:0}
.char-card .info .n{font-size:13px;font-weight:600}
.char-card .info .d{font-size:10px;color:#777}
.char-card .history{margin-top:6px;font-size:10px;color:#555;max-height:80px;overflow-y:auto}
.char-card .history .entry{padding:2px 0;border-bottom:1px dotted #1a1a1a}
.char-card .row{margin-top:8px}
.char-card select,.char-card input{font-size:11px;padding:4px 6px}
.char-card label{font-size:10px}
.progress-bar{width:100%;height:5px;background:#222;border-radius:3px;overflow:hidden;margin:10px 0}
.progress-bar .fill{height:100%;background:linear-gradient(90deg,#ff6b6b,#ffa500);border-radius:3px;transition:width .3s}
.log-box{background:#000;border-radius:6px;padding:8px;max-height:160px;overflow-y:auto;font-family:monospace;font-size:10px;color:#888}
.log-box .error{color:#ff6b6b}.log-box .info{color:#4dabf7}.log-box .success{color:#51cf66}
.stats{display:flex;gap:8px;flex-wrap:wrap}
.stat{background:#111;border-radius:6px;padding:8px 12px;min-width:80px}
.stat .l{font-size:9px;color:#777}.stat .v{font-size:16px;font-weight:700;color:#ffa500}.stat .v.g{color:#51cf66}
.badge{font-size:10px;padding:2px 6px;border-radius:8px}
.badge.ok{background:#51cf6622;color:#51cf66}.badge.no{background:#ff6b6b22;color:#ff6b6b}
.section-header{display:flex;align-items:center;justify-content:space-between;margin-bottom:8px}
.inline-form{display:flex;gap:6px;margin-top:8px}
.tabs{display:flex;gap:0;margin-bottom:12px;border-bottom:1px solid #2a2a4a}
.tab{padding:6px 14px;cursor:pointer;font-size:12px;color:#888;border-bottom:2px solid transparent}
.tab.active{color:#ffa500;border-bottom-color:#ffa500}
.help{font-size:10px;color:#555;margin-top:3px}
</style></head><body>
<div class="header">
  <h1>MiMo TTS</h1>
  <div class="row">
    <button class="btn btn-s btn-sm" onclick="showKeyPanel()">🔑 API Key</button>
    <button class="btn btn-s btn-sm" onclick="showLLMPanel()">🤖 LLM</button>
    <select id="apiModeSelect" onchange="switchMode(this.value)" style="width:auto;font-size:11px;padding:3px 6px">
      <option value="normal">按量付费</option><option value="tokenplan">Token Plan</option>
    </select>
    <span class="badge" id="keyBadge">查 Key</span>
  </div>
</div>

<!-- LLM 配置面板 -->
<div class="card" id="llmPanel" style="display:none;border-color:#4dabf744">
  <div class="section-header"><h2 style="margin:0">🤖 LLM 配置</h2>
    <button class="btn btn-s btn-sm" onclick="document.getElementById('llmPanel').style.display='none'">✕ 关闭</button>
  </div>
  <div class="row" style="margin-bottom:8px">
    <div class="grow"><label>LLM API Key</label><input type="password" id="llmKey" placeholder="留空则使用 TTS Key"></div>
  </div>
  <div class="row" style="margin-bottom:8px">
    <div class="grow"><label>LLM Base URL</label><input id="llmUrl" placeholder="留空则使用默认"></div>
  </div>
  <div class="row" style="margin-bottom:8px">
    <div class="grow"><label>LLM 模型</label><input id="llmModel" placeholder="留空则自动选择"></div>
    <button class="btn btn-s btn-sm" onclick="probeModels()">探测模型</button>
  </div>
  <div id="llmModelList" style="max-height:150px;overflow-y:auto;font-size:11px;margin-top:4px"></div>
  <div class="btn-row">
    <button class="btn btn-p btn-sm" onclick="saveLLMConfig()">保存 LLM 配置</button>
    <span class="help">LLM 配置独立于 TTS，仅影响角色识别和脚本划分</span>
  </div>
</div>

<!-- Key 配置面板 -->
<div class="card" id="keyPanel" style="display:none;border-color:#ff6b6b44">
  <div class="section-header"><h2 style="margin:0">🔑 API Key 配置</h2>
    <button class="btn btn-s btn-sm" onclick="document.getElementById('keyPanel').style.display='none'">✕ 关闭</button>
  </div>
  <div style="display:flex;gap:6px">
    <input type="password" id="apiKeyInput" placeholder="输入 API Key..." style="flex:1">
    <button class="btn btn-s btn-sm" onclick="document.getElementById('apiKeyInput').type=document.getElementById('apiKeyInput').type==='password'?'text':'password'">👁</button>
    <button class="btn btn-p btn-sm" onclick="saveApiKey()">保存 Key</button>
  </div>
  <div class="help">
    当前模式: <span id="keyModeLabel">按量付费</span> ·
    按量付费和 Token Plan 是<b>两套独立的 Key</b>，需分别配置 ·
    获取: <a href="https://platform.xiaomimimo.com" target="_blank" style="color:#ffa500">平台官网</a>
  </div>
  <div class="btn-row">
    <button class="btn btn-s btn-sm" onclick="copyKeyFromOtherMode()">📋 从另一模式复制 Key</button>
  </div>
  <p id="keyMsg" style="font-size:11px;margin-top:4px"></p>
</div>
<div class="container">

<!-- ===== 项目列表 ===== -->
<div class="page active" id="page-projects">
  <div class="card">
    <div style="display:flex;align-items:center;justify-content:space-between">
      <h2 style="margin:0">📚 我的有声书</h2>
      <button class="btn btn-p btn-sm" onclick="showCreate()">+ 新建</button>
    </div>
  </div>
  <div class="card" id="createForm" style="display:none">
    <h2>新建项目</h2>
    <input id="newTitle" placeholder="书名（如：三体）">
    <div class="btn-row">
      <button class="btn btn-p btn-sm" onclick="createProject()">创建</button>
      <button class="btn btn-s btn-sm" onclick="document.getElementById('createForm').style.display='none'">取消</button>
    </div>
  </div>
  <div class="proj-list" id="projList"></div>
  <div id="noProj" style="text-align:center;padding:40px;color:#555">暂无项目</div>
</div>

<!-- ===== 项目详情 ===== -->
<!-- ====== PAGE: 音色定制 ====== -->
<div class="page" id="page-voice-custom">
  <div class="card"><div style="display:flex;align-items:center;justify-content:space-between">
    <h2 style="margin:0">🎤 音色定制 - <span id="vcCharName"></span></h2>
    <button class="btn btn-s btn-sm" onclick="showPage('page-project');renderChars()">返回项目</button>
  </div></div>
  <div class="card">
    <h2>音色描述（VoiceDesign 提示词）</h2>
    <textarea id="vcDesc" style="min-height:80px" placeholder="描述这个角色的声音特征..."></textarea>
    <div class="help">描述维度：性别年龄、音色质感、情绪语气、语速节奏。1-4句话即可。</div>
  </div>
  <div class="card">
    <h2>试听样本</h2>
    <div class="row"><div class="grow"><input id="vcSampleText" value="这是为角色定制的专属音色。每一个字，都融入了角色的灵魂与故事。"></div></div>
    <div class="btn-row">
      <button class="btn btn-p btn-sm" id="btnVCGen" onclick="genVoiceSample()">生成试听</button>
      <span id="vcGenCost" style="font-size:10px;color:#888"></span>
    </div>
    <audio id="vcAudio" controls style="width:100%;margin-top:10px;display:none"></audio>
  </div>
  <div class="card">
    <h2>上传音频样本（可选）</h2>
    <div class="help">上传一段 WAV/MP3 音频作为音色克隆参考。效果比文本描述更稳定。</div>
    <input type="file" id="vcUpload" accept=".wav,.mp3" onchange="uploadVoiceSample()">
  </div>
  <div class="btn-row">
    <button class="btn btn-p" onclick="saveVoiceCustom()">💾 保存音色</button>
    <button class="btn btn-d" onclick="removeVoiceCustom()">移除定制</button>
  </div>
</div>

<!-- ====== PAGE: 章节分段编辑 ====== -->
<div class="page" id="page-chap-edit">
  <div class="card"><div style="display:flex;align-items:center;justify-content:space-between">
    <h2 style="margin:0" id="chapEditTitle">章节分段编辑</h2>
    <button class="btn btn-s btn-sm" onclick="goBackFromChapEdit()">返回项目</button>
  </div></div>
  <div class="card">
    <div id="segEditList"></div>
    <div class="btn-row">
      <button class="btn btn-p btn-sm" onclick="saveSegEdit()">保存修改</button>
      <button class="btn btn-s btn-sm" onclick="addSegRow()">+ 添加分段</button>
    </div>
  </div>
</div>

<div class="page" id="page-project">
  <div class="card"><div style="display:flex;align-items:center;justify-content:space-between">
    <h2 style="margin:0" id="projTitle">项目</h2>
    <button class="btn btn-s btn-sm" onclick="goProjects()">← 返回列表</button>
  </div></div>

  <!-- 导入整本书 -->
  <div class="card" id="importBookCard">
    <div class="section-header"><h2 style="margin:0">📥 导入书籍</h2>
      <button class="btn btn-s btn-sm" onclick="document.getElementById('importBookCard').style.display='none'">已导入，隐藏</button>
    </div>
    <div class="row" style="margin-bottom:8px"><label class="btn btn-s" style="cursor:pointer;width:auto">📂 上传整本书 (.txt) <input type="file" id="bookFile" accept=".txt,.md" style="display:none" onchange="uploadBookFile()"></label></div>
    <textarea id="bookText" placeholder="或在此粘贴整本小说...&#10;&#10;第一章 标题&#10;正文...&#10;&#10;第二章 标题&#10;正文...&#10;&#10;支持自动识别 '第X章' 'Chapter X' 等章节标题"></textarea>
    <div class="btn-row">
      <button class="btn btn-p" id="btnImportBook" onclick="importBook()">自动分章 & 导入</button>
      <span class="help">自动识别章节标题并按章节拆分</span>
    </div>
  </div>

  <!-- 章节 -->
  <div class="card">
    <div class="section-header"><h2 style="margin:0;cursor:pointer" onclick="toggleSection('chapterBody')">📖 章节 ▾</h2>
      <div class="row">
        <button class="btn btn-s btn-sm" onclick="selectAllChapters(true)">全选</button>
        <button class="btn btn-s btn-sm" onclick="selectAllChapters(false)">取消全选</button>
        <button class="btn btn-p btn-sm" onclick="showAddChapter()">+ 添加章节</button>
      </div>
    </div>
    <div id="chapterBody">
    <div id="chapterList"></div>
    <div id="addChapterForm" style="display:none" class="inline-form">
      <input id="chapTitle" placeholder="章节标题（如：第一章 深夜来客）" style="flex:1">
      <textarea id="chapText" placeholder="章节正文..." style="min-height:60px"></textarea>
      <div class="help">或选择文件：<input type="file" id="chapFile" accept=".txt,.md" onchange="loadChapFile()" style="width:auto"></div>
      <div class="btn-row">
        <button class="btn btn-p btn-sm" onclick="addChapter()">添加</button>
        <button class="btn btn-s btn-sm" onclick="document.getElementById('addChapterForm').style.display='none'">取消</button>
      </div>
    </div>
    <div id="chapStats" class="stats" style="margin-top:10px"></div>
    <div class="help" style="margin-top:8px">✓ 勾选的章节参与合成 · 点击章节标题进入分段编辑</div>
    </div>
  </div>

  <!-- 角色卡 -->
  <div class="card">
    <div class="section-header"><h2 style="margin:0;cursor:pointer" onclick="toggleSection('charBody')">🎭 角色卡 ▾</h2>
      <div class="row">
        <span class="badge ok" id="charBadge" style="display:none">已建档</span>
        <label style="display:flex;align-items:center;gap:4px;cursor:pointer;font-size:11px;color:#aaa;margin-right:8px">
        <input type="checkbox" id="detectWithParse" onchange="S.detectWithParse=this.checked" style="width:auto"> LLM 划分对话
      </label>
      <label style="display:flex;align-items:center;gap:4px;cursor:pointer;font-size:11px;color:#ff6b6b;margin-right:8px">
        <input type="checkbox" id="forceRedetect" onchange="S.forceRedetect=this.checked" style="width:auto"> 强制重新识别
      </label>
      <button class="btn btn-p btn-sm" id="btnDetect" onclick="detectChars()">🔍 识别角色</button>
        <button class="btn btn-s btn-sm" id="btnAddManual" onclick="addManualChar()">+ 手动添加</button>
        <button class="btn btn-d btn-sm" id="btnDelChars" style="display:none" onclick="deleteAllChars()">清空角色卡</button>
      </div>
    </div>
    <div id="charBody">
    <div id="charCards"></div>
    <div id="charActs" class="btn-row" style="display:none">
      <button class="btn btn-g btn-sm" onclick="saveChars()">💾 保存角色卡</button>
      <span class="help">角色卡持久化到项目中，下次打开直接复用</span>
    </div>
  </div>

  <!-- 合成 -->
  <div class="card">
    <h2>🎙 合成有声书</h2>
    <div id="synthReady"><div class="btn-row">
      <label style="display:flex;align-items:center;gap:4px;cursor:pointer;font-size:12px;color:#aaa">
        <input type="checkbox" id="useLLMParse" onchange="S.useLLMParse=this.checked" style="width:auto"> LLM 脚本解析（更准确，有额外费用）
      </label>
      <button class="btn btn-p" onclick="startSynth()">开始合成</button>
    </div></div></div>
    <div id="synthProgress" style="display:none">
      <div class="progress-bar"><div class="fill" id="pFill" style="width:0%"></div></div>
      <p style="text-align:center;font-size:12px;color:#888" id="pText">...</p>
      <div class="stats"><div class="stat"><div class="l">Token</div><div class="v" id="sTokens">0</div></div>
      <div class="stat"><div class="l">费用</div><div class="v g" id="sCost">¥0</div></div>
      <div class="stat"><div class="l">时长</div><div class="v" id="sDur">0s</div></div></div>
      <div class="log-box" id="logBox"></div>
    </div>
    <div id="synthDone" style="display:none">
      <div class="stats"><div class="stat"><div class="l">Token</div><div class="v" id="fTokens">0</div></div>
      <div class="stat"><div class="l">费用</div><div class="v g" id="fCost">¥0</div></div>
      <div class="stat"><div class="l">时长</div><div class="v" id="fDur">0</div></div></div>
      <div class="btn-row"><button class="btn btn-p" onclick="downloadAudio()">⬇ 下载</button>
      <button class="btn btn-s" onclick="resetSynth()">重新合成</button></div>
    </div>
  </div>
</div>
</div>

<script>
let S={apiMode:localStorage.getItem('mimo_api_mode')||'normal',projects:[],pid:null,proj:null,taskId:null,outFile:null,useLLMParse:false,detectWithParse:false,forceRedetect:false};
init();
async function init(){
  document.getElementById('apiModeSelect').value=S.apiMode;
  await checkKey();await listProjects();
}

// ── 安全 fetch 封装 ──
async function api(url, opts={}){
  try{
    const r=await fetch(url,opts);
    const text=await r.text();
    if(!text||!text.trim()) throw new Error('服务器返回空响应');
    try{return JSON.parse(text);}
    catch(e){throw new Error('非JSON响应: '+text.substring(0,200));}
  }catch(e){
    if(e.message.includes('Failed to fetch')) throw new Error('无法连接服务器，请确认 python app.py 已启动');
    throw e;
  }
}

// Key
async function checkKey(){
  try{
    const d=await api('/api/key-status?mode='+S.apiMode);
    const b=document.getElementById('keyBadge');
    b.textContent=d.has_key?'Key OK':'无 Key';b.className='badge '+(d.has_key?'ok':'no');
    document.getElementById('keyModeLabel').textContent=S.apiMode==='tokenplan'?'Token Plan':'按量付费';
    if(d.has_key&&d.key_masked){
      document.getElementById('apiKeyInput').placeholder='已配置: '+d.key_masked;
    }else{
      document.getElementById('apiKeyInput').placeholder=S.apiMode==='tokenplan'?'输入 MIMO_TOKEN_PLAN_KEY...':'输入 MIMO_API_KEY...';
    }
    return d.has_key;
  }catch(e){console.error('checkKey:',e);return false;}
}
function switchMode(m){
  S.apiMode=m; document.getElementById('apiModeSelect').value=m;
  localStorage.setItem('mimo_api_mode', m);  // 持久化到浏览器
  checkKey().then(hasKey=>{
    if(!hasKey){
      document.getElementById('keyPanel').style.display='block';
      document.getElementById('apiKeyInput').value='';
      document.getElementById('apiKeyInput').placeholder=m==='tokenplan'?'输入 MIMO_TOKEN_PLAN_KEY...':'输入 MIMO_API_KEY...';
      document.getElementById('keyMsg').innerHTML='<span style="color:#ff6b6b">切换模式后需要配置对应的 Key</span>';
    }
  });
}
function showKeyPanel(){
  document.getElementById('keyPanel').style.display='block';
  document.getElementById('llmPanel').style.display='none';
  checkKey();
}
function showLLMPanel(){
  document.getElementById('llmPanel').style.display='block';
  document.getElementById('keyPanel').style.display='none';
  // Load stored LLM config
  var cfg=JSON.parse(localStorage.getItem('mimo_llm_config')||'{}');
  document.getElementById('llmKey').value=cfg.key||'';
  document.getElementById('llmUrl').value=cfg.url||'';
  document.getElementById('llmModel').value=cfg.model||'';
}
function saveLLMConfig(){
  var cfg={key:document.getElementById('llmKey').value.trim(),url:document.getElementById('llmUrl').value.trim(),model:document.getElementById('llmModel').value.trim()};
  if(!cfg.key&&!cfg.url&&!cfg.model){localStorage.removeItem('mimo_llm_config');}
  else{localStorage.setItem('mimo_llm_config',JSON.stringify(cfg));}
  alert('LLM 配置已保存!');
}
async function probeModels(){
  var url=document.getElementById('llmUrl').value.trim()||'';
  var key=document.getElementById('llmKey').value.trim()||'';
  if(!url&&!key){alert('请先填写 LLM Base URL 或 Key');return}
  var d=await api('/api/list-models',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({url:url,key:key})});
  if(d.error){alert(d.error);return}
  var el=document.getElementById('llmModelList');
  el.innerHTML='';d.models.forEach(m=>{var div=document.createElement('div');div.textContent=m;div.style.cssText='padding:2px 4px;cursor:pointer;color:#4dabf7';div.onclick=function(){document.getElementById('llmModel').value=m};el.appendChild(div)});
}
async function copyKeyFromOtherMode(){
  const otherMode=S.apiMode==='tokenplan'?'normal':'tokenplan';
  const d=await api('/api/key-status?mode='+otherMode);
  if(d.has_key){
    document.getElementById('apiKeyInput').value='';
    document.getElementById('apiKeyInput').placeholder='另一模式已配置 Key，直接点击"保存 Key"复用';
    // 自动用另一模式的 key 保存到当前模式
    const d2=await api('/api/copy-key',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({from:otherMode,to:S.apiMode})});
    if(d2.ok){document.getElementById('keyMsg').innerHTML='<span style="color:#51cf66">Key 已从另一模式复制!</span>';checkKey();}
  }else{
    document.getElementById('keyMsg').innerHTML='<span style="color:#ff6b6b">另一模式也未配置 Key，请手动输入</span>';
  }
}
async function saveApiKey(){
  const key=document.getElementById('apiKeyInput').value.trim();
  if(!key){document.getElementById('keyMsg').innerHTML='<span style="color:#ff6b6b">请输入 Key</span>';return}
  const d=await api('/api/set-key',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({key,mode:S.apiMode})});
  if(d.ok){
    document.getElementById('keyMsg').innerHTML='<span style="color:#51cf66">Key 已保存!</span>';
    document.getElementById('apiKeyInput').value='';
    checkKey();
  }else{
    document.getElementById('keyMsg').innerHTML='<span style="color:#ff6b6b">保存失败</span>';
  }
}

// 项目列表
async function listProjects(){
  S.projects=await api('/api/projects');
  const el=document.getElementById('projList');
  if(!S.projects.length){el.innerHTML='';document.getElementById('noProj').style.display='block';return}
  document.getElementById('noProj').style.display='none';
  el.innerHTML=S.projects.map(p=>`<div class="proj-card" onclick="openProject('${p.id}')">
    <h3>${p.book_title||'未命名'}</h3>
    <div class="meta">${(p.updated_at||'').substring(0,16)} · ${p.chapter_count||0} 章 · ${(p.total_chars||0).toLocaleString()} 字</div>
    <div class="chars">角色: ${p.characters?.length||0} · 旁白: ${p.narrator_voice||''} · LLM Token: ${(p.llm_tokens||0).toLocaleString()}</div>
    <button class="btn btn-d btn-xs" onclick="event.stopPropagation();deleteProject('${p.id}')" style="margin-top:6px">删除项目</button>
  </div>`).join('');
}
function showCreate(){document.getElementById('createForm').style.display='block';}
async function createProject(){
  const t=document.getElementById('newTitle').value.trim();if(!t){alert('书名不能为空');return}
  const d=await api('/api/projects',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({book_title:t})});
  document.getElementById('createForm').style.display='none';document.getElementById('newTitle').value='';
  await listProjects();openProject(d.id);
}
// 可折叠
function toggleSection(id){var e=document.getElementById(id);e.style.display=e.style.display==='none'?'block':'none'}

// 章节分段编辑
let S_chapEdit={pid:null,idx:null,segments:[]};
async function openChapEdit(idx){
  S_chapEdit.pid=S.pid;S_chapEdit.idx=idx;
  const d=await api('/api/projects/'+S.pid+'/chapters/'+idx+'/segments');
  S_chapEdit.segments=d.segments||[];
  document.getElementById('chapEditTitle').textContent=d.title||'章节分段';
  renderSegEdit();showPage('page-chap-edit');
}
function renderSegEdit(){
  const segs=S_chapEdit.segments;
  const chars=S.proj.characters||[];
  const allNames=['旁白'].concat(chars.map(c=>c.name));
  document.getElementById('segEditList').innerHTML=segs.map((s,i)=>{let opts=allNames.map(n=>'<option value="'+n.replace(/"/g,'&quot;')+'"'+(s.speaker===n?' selected':'')+'>'+n+'</option>').join('');return'<div class="char-card" style="margin-bottom:4px"><div class="row" style="width:100%"><select onchange="S_chapEdit.segments['+i+'].speaker=this.value" style="width:120px;font-size:11px">'+opts+'</select><textarea onchange="S_chapEdit.segments['+i+'].text=this.value" style="flex:1;min-height:30px;font-size:11px">'+s.text.replace(/</g,'&lt;')+'</textarea><button class="btn btn-s btn-xs" onclick="moveSeg('+i+',-1)"'+(i===0?' disabled':'')+'>▲</button><button class="btn btn-s btn-xs" onclick="moveSeg('+i+',1)"'+(i===segs.length-1?' disabled':'')+'>▼</button><button class="btn btn-d btn-xs" onclick="S_chapEdit.segments.splice('+i+',1);renderSegEdit()">x</button></div></div>'}).join('');
}
function addSegRow(){S_chapEdit.segments.push({speaker:'旁白',text:''});renderSegEdit()}
async function saveSegEdit(){
  await api('/api/projects/'+S_chapEdit.pid+'/chapters/'+S_chapEdit.idx+'/segments',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({segments:S_chapEdit.segments})});
  alert('已保存!')
}
function goBackFromChapEdit(){showPage('page-project');renderChapters()}
async function deleteProject(pid){if(!confirm('确认删除此项目？所有数据将丢失！'))return;await api('/api/projects/'+pid,{method:'DELETE'});await listProjects()}
function goProjects(){showPage('page-projects');listProjects();}
function showPage(id){document.querySelectorAll('.page').forEach(p=>p.classList.remove('active'));document.getElementById(id).classList.add('active');}

// 项目详情
async function openProject(pid){S.pid=pid;await refreshProj();showPage('page-project');renderChapters();renderChars();}
async function refreshProj(){S.proj=await api('/api/projects/'+S.pid);document.getElementById('projTitle').textContent=S.proj.book_title||'未命名';}

// 章节管理
function showAddChapter(){document.getElementById('addChapterForm').style.display='block';document.getElementById('chapTitle').value='';document.getElementById('chapText').value='';}
function loadChapFile(){const f=document.getElementById('chapFile').files[0];if(!f)return;f.text().then(t=>{document.getElementById('chapText').value=t})}
async function addChapter(){
  const title=document.getElementById('chapTitle').value.trim()||'未命名章节';
  const text=document.getElementById('chapText').value.trim();
  if(!text){alert('请输入章节内容');return}
  await api('/api/projects/'+S.pid+'/chapters',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({title,text})});
  document.getElementById('addChapterForm').style.display='none';await refreshProj();renderChapters();
}
async function deleteChapter(idx){
  if(!confirm('删除此章节？'))return;
  await api('/api/projects/'+S.pid+'/chapters/'+idx,{method:'DELETE'});await refreshProj();renderChapters();
}
function uploadBookFile(){const f=document.getElementById('bookFile').files[0];if(!f)return;f.text().then(t=>{document.getElementById('bookText').value=t});}
async function importBook(){
  const text=document.getElementById('bookText').value.trim();
  if(!text){alert('请先上传文件或粘贴文本');return}
  const btn=document.getElementById('btnImportBook');btn.disabled=true;btn.textContent='正在分章...';
  try{
    const d=await api('/api/projects/'+S.pid+'/import-book',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text})});
    S.proj=d; renderChapters();
    document.getElementById('bookText').value='';
    document.getElementById('importBookCard').style.display='none';
  }catch(e){alert('导入失败: '+e.message)}
  finally{btn.disabled=false;btn.textContent='自动分章 & 导入';}
}
function selectAllChapters(sel){const chs=S.proj.chapters||[];chs.forEach(c=>c._selected=sel);saveChapterSelection();renderChapters();}
async function saveChapterSelection(){await api('/api/projects/'+S.pid+'/save-chapters',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({chapters:S.proj.chapters})});}
function renderChapters(){
  const chs=S.proj.chapters||[];
  const el=document.getElementById('chapterList');
  if(!chs.length){el.innerHTML='<p style="color:#555;font-size:12px">暂无章节，先导入整本书或手动添加</p>';document.getElementById('chapStats').innerHTML='';return}
  chs.forEach(c=>{if(c._selected===undefined)c._selected=true;});
  el.innerHTML=chs.map((c,i)=>`<div class="chapter-item" style="${c._selected?'':'opacity:.4'}">
    <input type="checkbox" ${c._selected?'checked':''} onchange="S.proj.chapters[${i}]._selected=this.checked;saveChapterSelection();renderChapters()" style="width:auto;flex-shrink:0">
    <span class="idx">#${i+1}</span><span class="title" style="cursor:pointer;color:#ffa500" onclick="event.stopPropagation();openChapEdit(${i})">${c.title}</span>
    <span class="chars">${(c.chars||0).toLocaleString()} 字</span>
    <button class="btn btn-d btn-xs" onclick="event.stopPropagation();deleteChapter(${i})">✕</button>
  </div>`).join('');
  const sel=chs.filter(c=>c._selected!==false);
  const total=sel.reduce((s,c)=>s+(c.chars||0),0);
  document.getElementById('chapStats').innerHTML=`<div class="stat"><div class="l">章节</div><div class="v">${chs.length}</div></div><div class="stat"><div class="l">已选</div><div class="v">${sel.length}</div></div><div class="stat"><div class="l">总字数</div><div class="v">${total.toLocaleString()}</div></div>`;
}

// 角色卡
function renderChars(){
  const chars=S.proj.characters||[];
  const el=document.getElementById('charCards');
  const badge=document.getElementById('charBadge');
  if(!chars.length){el.innerHTML='<p style="color:#555;font-size:12px">点击"识别角色"自动分析，或"手动添加"</p>';badge.style.display='none';document.getElementById('charActs').style.display='none';return}
  badge.style.display='inline';document.getElementById('charActs').style.display='flex';document.getElementById('btnDelChars').style.display='inline';
  const voices={{ voices|tojson }};
  const vkeys=Object.keys(voices);
  const totalChaps=(S.proj.chapters||[]).length;
  const nv=S.proj.narrator_voice||'茉莉'; const ns=S.proj.narrator_style||'';
  el.innerHTML='<div style="font-size:11px;color:#888;margin-bottom:8px;display:flex;gap:8px;align-items:center;flex-wrap:wrap">'+
    '<span>旁白:</span>'+
    '<select onchange="S.proj.narrator_voice=this.value" style="width:auto;font-size:11px;padding:3px 6px;background:#222;color:#eee;border:1px solid #444">'+vkeys.map(v=>`<option value="${v}" ${nv===v?'selected':''}>${v}</option>`).join('')+'</select>'+
    '<input value="'+ns.replace(/"/g,'&quot;')+'" onchange="S.proj.narrator_style=this.value" placeholder="旁白风格提示词（如：沉稳大气的男声..." style="flex:1;min-width:200px;font-size:11px;padding:4px 6px;background:#111;color:#eee;border:1px solid #333">'+
    '</div>'+
    chars.map((c,i)=>`<div class="char-card">
      <div class="head">
        <div class="avatar ${c.gender==='女'?'f':'m'}">${c.name[0]}</div>
        <div class="info"><div class="n">${c.name}</div><div class="d">${c.gender}·${c.age}·${c.personality}·${c.role}</div></div>
        <button class="btn btn-d btn-xs" onclick="event.stopPropagation();deleteChar(${i})" style="flex-shrink:0">✕</button>
      </div>
      <div class="row">
        <button class="btn btn-s btn-xs" onclick="event.stopPropagation();openVoiceCustom(${i})" style="font-size:10px">🎤 定制音色</button>
        ${c.voice_sample_file?'<span style="font-size:10px;color:#51cf66">已定制</span>':''}
      </div>
      <div class="row">
        <label>音色:</label><select onchange="S.proj.characters[${i}].assigned_voice=this.value">${vkeys.map(v=>`<option value="${v}" ${c.assigned_voice===v?'selected':''}>${v}</option>`).join('')}</select>
        <label>风格:</label><input value="${c.speaking_style||''}" onchange="S.proj.characters[${i}].speaking_style=this.value" placeholder="说话风格">
      </div>
      <div class="row">
        <label>别称:</label><input value="${(c.aliases||[]).join(', ')}" onchange="S.proj.characters[${i}].aliases=this.value.split(',').map(s=>s.trim()).filter(s=>s)" placeholder="逗号分隔，如：慕容富, 慕容复" style="flex:1">
      </div>
      <div class="row">
        <label>成长记录:</label><input placeholder="如：第3章后性格转变..." style="flex:1" id="charNote${i}" onkeydown="if(event.key==='Enter')addCharNote(${i})">
        <button class="btn btn-s btn-sm" onclick="addCharNote(${i})">+ 添加</button>
      </div>
      ${(c.history||[]).length?`<div class="history">${c.history.map(h=>`<div class="entry">Ch${h.chapter}: ${h.note}</div>`).join('')}</div>`:''}
    </div>`).join('');
}
function addCharNote(ci){
  const inp=document.getElementById('charNote'+ci);
  const note=inp.value.trim();if(!note)return;
  const c=S.proj.characters[ci];
  if(!c.history)c.history=[];
  const chNum=(S.proj.chapters||[]).length||1;
  c.history.push({chapter:chNum,note,time:new Date().toISOString().substring(0,16)});
  inp.value='';renderChars();
}
function addManualChar(){
  const n=prompt('角色名:');if(!n)return;
  S.proj.characters.push({name:n,gender:'男',age:'青年',personality:'',role:'配角',speaking_style:'',assigned_voice:'苏打',history:[],aliases:[]});
  renderChars();document.getElementById('charActs').style.display='flex';
}
async function deleteChar(i){if(confirm('删除角色 '+S.proj.characters[i].name+' ？')){S.proj.characters.splice(i,1);await saveCharsSilent();renderChars()}}
async function saveCharsSilent(){await api('/api/projects/'+S.pid+'/save-chars',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({characters:S.proj.characters,narrator_voice:S.proj.narrator_voice,narrator_style:S.proj.narrator_style})})}
async function deleteAllChars(){if(confirm('确认清空全部角色卡？')){S.proj.characters=[];await api('/api/projects/'+S.pid+'/save-chars',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({characters:[],narrator_voice:S.proj.narrator_voice,narrator_style:S.proj.narrator_style})});renderChars()}}
// 音色定制
let S_vc={charIdx:null,audioB64:null};
function openVoiceCustom(i){
  S_vc.charIdx=i;S_vc.audioB64=null;
  var c=S.proj.characters[i];
  document.getElementById('vcCharName').textContent=c.name;
  document.getElementById('vcDesc').value=c.speaking_style||c.personality||'';
  document.getElementById('vcGenCost').textContent='';
  document.getElementById('vcAudio').style.display='none';
  showPage('page-voice-custom');
}
async function genVoiceSample(){
  var desc=document.getElementById('vcDesc').value.trim();
  if(!await checkKey()){alert('请先配置 API Key！\\n\\n即将跳转到小米 MiMo 开放平台注册页面。');window.open('https://platform.xiaomimimo.com?ref=V25WQB','_blank');return}
  if(!desc){alert('请输入音色描述');return}
  var btn=document.getElementById('btnVCGen');btn.disabled=true;btn.textContent='生成中...';
  try{
    var d=await api('/api/voice-design',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({description:desc,sample_text:document.getElementById('vcSampleText').value,mode:S.apiMode})});
    if(d.error){alert(d.error);return}
    S_vc.audioB64=d.audio_b64;
    var audio=document.getElementById('vcAudio');
    audio.src='data:audio/wav;base64,'+d.audio_b64;
    audio.style.display='block';audio.play();
    document.getElementById('vcGenCost').textContent='试听已生成';
  }catch(e){alert(e.message)}
  finally{btn.disabled=false;btn.textContent='重新生成';}
}
function uploadVoiceSample(){
  var f=document.getElementById('vcUpload').files[0];if(!f)return;
  var reader=new FileReader();
  reader.onload=function(){
    S_vc.audioB64=reader.result.split(',')[1];
    var audio=document.getElementById('vcAudio');
    audio.src=reader.result;audio.style.display='block';audio.play();
    document.getElementById('vcGenCost').textContent='已上传音频样本';
  };
  reader.readAsDataURL(f);
}
async function saveVoiceCustom(){
  if(!S_vc.audioB64){alert('请先生成试听或上传音频');return}
  // 保存到服务端文件，不在 project.json 里塞 base64
  var d=await api('/api/save-voice-sample',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({pid:S.pid,char_name:S.proj.characters[S_vc.charIdx].name,audio_b64:S_vc.audioB64})});
  if(d.ok){
    S.proj.characters[S_vc.charIdx].voice_sample_file=d.file;
    S.proj.characters[S_vc.charIdx].voice_model='voiceclone';
    // 同步保存音色描述到角色卡
    S.proj.characters[S_vc.charIdx].speaking_style=document.getElementById('vcDesc').value;
    renderChars();showPage('page-project');
    alert('音色已保存!');
  }
}
async function removeVoiceCustom(){
  if(!confirm('移除该角色的定制音色？'))return;
  var c=S.proj.characters[S_vc.charIdx];
  if(c.voice_sample_file){
    await api('/api/delete-voice-sample',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({pid:S.pid,file:c.voice_sample_file})});
  }
  delete c.voice_sample_file;delete c.voice_model;
  renderChars();showPage('page-project');
}
async function detectChars(){
  if(!await checkKey()){alert('请先配置 API Key！\\n\\n即将跳转到小米 MiMo 开放平台注册页面。');window.open('https://platform.xiaomimimo.com?ref=V25WQB','_blank');return}
  const text=(S.proj.chapters||[]).map(c=>c.text).join('\n\n');
  if(!text){alert('请先添加章节');return}
  const btn=document.getElementById('btnDetect');btn.disabled=true;btn.textContent='分析中...';
  try{
    const d=await api('/api/projects/'+S.pid+'/detect-chars',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({mode:S.apiMode,text,with_parse:S.detectWithParse,force:S.forceRedetect,llm_config:JSON.parse(localStorage.getItem('mimo_llm_config')||'{}')})});
    if(d.error){alert(d.error);return}
    // 合并已有角色的 history
    const oldChars=S.proj.characters||[];
    d.characters.forEach(nc=>{const oc=oldChars.find(c=>c.name===nc.name);if(oc&&oc.history)nc.history=oc.history});
    S.proj.characters=d.characters;S.proj.narrator_voice=d.narrator_voice||S.proj.narrator_voice;
    S.proj.narrator_style=d.narrator_style||S.proj.narrator_style;
    renderChars();await saveChars();
  }finally{btn.disabled=false;btn.textContent='🔄 重新识别';}
}
async function saveChars(){
  await api('/api/projects/'+S.pid+'/save-chars',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({
    characters:S.proj.characters,narrator_voice:S.proj.narrator_voice,narrator_style:S.proj.narrator_style
  })});
  alert('角色卡已保存到项目!');
}

// 合成
async function startSynth(){
  if(!await checkKey()){alert('请先配置 API Key！\\n\\n即将跳转到小米 MiMo 开放平台注册页面。');window.open('https://platform.xiaomimimo.com?ref=V25WQB','_blank');return}
  document.getElementById('synthReady').style.display='none';document.getElementById('synthProgress').style.display='block';
  document.getElementById('pFill').style.width='0%';document.getElementById('logBox').innerHTML='';
  const d=await api('/api/projects/'+S.pid+'/synthesize',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({mode:S.apiMode,use_llm:S.useLLMParse,llm_config:JSON.parse(localStorage.getItem('mimo_llm_config')||'{}')})});
  if(d.error){alert(d.error);return}
  S.taskId=d.task_id;pollProgress();
}
async function pollProgress(){
  const d=await api('/api/task-progress/'+S.taskId);
  const pct=d.total?Math.round(d.current/d.total*100):0;
  document.getElementById('pFill').style.width=pct+'%';
  document.getElementById('pText').textContent=`${d.current||0}/${d.total||0} · ${d.status}`;
  document.getElementById('sTokens').textContent=(d.tokens||0).toLocaleString();
  document.getElementById('sCost').textContent=d.is_free?'¥0(免费)':'¥'+(d.cost||0).toFixed(2);
  document.getElementById('sDur').textContent=(d.duration||0).toFixed(0)+'s';
  if(d.log){const b=document.getElementById('logBox');b.innerHTML=d.log.map(l=>`<div class="${l.level}">${l.msg}</div>`).join('');b.scrollTop=b.scrollHeight}
  if(d.status==='done'){
    S.outFile=d.file;S.outFiles=d.files;document.getElementById('fTokens').textContent=(d.tokens||0).toLocaleString();
    document.getElementById('fCost').textContent=d.is_free?'¥0(免费)':'¥'+(d.cost||0).toFixed(2);
    document.getElementById('fDur').textContent=(d.duration||0).toFixed(0)+'s';
    document.getElementById('synthProgress').style.display='none';document.getElementById('synthDone').style.display='block';
  }else if(d.status==='error'){document.getElementById('logBox').innerHTML+=`<div class="error">${d.error}</div>`}
  else{setTimeout(pollProgress,1500)}
}
function downloadAudio(){if(S.outFiles&&S.outFiles.length){S.outFiles.forEach(f=>window.open('/api/download/'+f))}else if(S.outFile)window.open('/api/download/'+S.outFile)}
function resetSynth(){document.getElementById('synthDone').style.display='none';document.getElementById('synthReady').style.display='block'}
</script></body></html>"""

# ── 全局错误处理 ──
@app.errorhandler(Exception)
def handle_all(e):
    """所有未捕获异常返回 JSON"""
    return jsonify({"error": str(e)}), 500

@app.errorhandler(404)
def handle_404(e):
    return jsonify({"error": "not found"}), 404

# ── 路由 ──
@app.route("/")
def index():
    return render_template_string(HTML, voices={k: v["voice_id"] for k, v in PRESET_VOICES.items()})

@app.route("/api/key-status")
def key_status():
    key = _load_api_key(request.args.get("mode", "normal"))
    masked = ""
    if key and len(key) > 8:
        masked = key[:8] + "..." + key[-4:]
    elif key:
        masked = key[:4] + "..."
    return jsonify({"has_key": bool(key), "key_masked": masked})

@app.route("/api/copy-key", methods=["POST"])
def copy_key():
    """将一种模式的 Key 复制到另一种模式"""
    d = request.get_json()
    from_mode = d.get("from", "normal")
    to_mode = d.get("to", "tokenplan")
    key = _load_api_key(from_mode)
    if not key:
        return jsonify({"ok": False, "error": "源模式无 Key"})
    to_env = MIMO_TOKEN_PLAN_KEY_ENV if to_mode == "tokenplan" else MIMO_API_KEY_ENV
    os.environ[to_env] = key
    p = Path(".env")
    existing = p.read_text(encoding="utf-8") if p.exists() else ""
    if to_env in existing:
        existing = re.sub(rf"^{to_env}=.*$", f"{to_env}={key}", existing, flags=re.MULTILINE)
    else:
        existing = existing.rstrip() + f"\n{to_env}={key}\n"
    p.write_text(existing, encoding="utf-8")
    return jsonify({"ok": True})

@app.route("/api/set-key", methods=["POST"])
def set_key():
    d = request.get_json()
    key = d.get("key", "").strip()
    mode = d.get("mode", "normal")
    env_var = MIMO_TOKEN_PLAN_KEY_ENV if mode == "tokenplan" else MIMO_API_KEY_ENV
    if key:
        os.environ[env_var] = key
        p = Path(".env")
        existing = p.read_text(encoding="utf-8") if p.exists() else ""
        if env_var in existing:
            existing = re.sub(rf"^{env_var}=.*$", f"{env_var}={key}", existing, flags=re.MULTILINE)
        else:
            existing = existing.rstrip() + f"\n{env_var}={key}\n"
        p.write_text(existing, encoding="utf-8")
    return jsonify({"ok": True})

# ── 项目 CRUD ──
@app.route("/api/projects", methods=["GET"])
def list_projects():
    return jsonify(_list_projects())

@app.route("/api/projects", methods=["POST"])
def create_project():
    d = request.get_json()
    pid = uuid.uuid4().hex[:8]
    proj = {"book_title": d.get("book_title", "未命名"), "chapters": [], "total_chars": 0,
            "narrator_voice": NARRATOR_VOICE, "narrator_style": "", "characters": [],
            "created_at": datetime.now().isoformat(timespec="seconds"), "updated_at": ""}
    _save_project(pid, proj)
    return jsonify({"id": pid, **proj})

@app.route("/api/projects/<pid>")
def get_project(pid):
    p = _load_project(pid)
    return jsonify(p) if p else (jsonify({"error": "not found"}), 404)

@app.route("/api/projects/<pid>", methods=["DELETE"])
def delete_project(pid):
    # 删除音频文件
    proj = _load_project(pid, load_chapters=False)
    if proj:
        audio_dir = STATIC_DIR / re.sub(r'[\\/*?:"<>|]', '', proj.get("book_title", ""))
        if audio_dir.exists():
            shutil.rmtree(audio_dir)
    # 删除项目数据
    d = _proj_dir(pid)
    if d.exists(): shutil.rmtree(d)
    return jsonify({"ok": True})

# ── 章节管理 ──
@app.route("/api/projects/<pid>/import-book", methods=["POST"])
def import_book(pid):
    """导入整本书并自动分章。"""
    proj = _load_project(pid)
    if not proj: return jsonify({"error": "not found"}), 404
    d = request.get_json(silent=True) or {}
    text = d.get("text", "")
    if not text: return jsonify({"error": "无文本"}), 400
    # 使用 text_chunker 的章节检测
    chapters = detect_chapter_headers(text)
    if not chapters or len(chapters) <= 1:
        # 如果只检测到一个标题或不检测，整个文本作为一章
        title = chapters[0]["title"] if chapters else "全文"
        proj["chapters"] = [{"title": title, "text": text.strip(), "chars": len(text),
                             "imported_at": datetime.now().isoformat(timespec="seconds")}]
    else:
        proj["chapters"] = [
            {"title": ch["title"], "text": ch["content"], "chars": len(ch["content"]),
             "imported_at": datetime.now().isoformat(timespec="seconds")}
            for ch in chapters
        ]
    proj["total_chars"] = sum(c["chars"] for c in proj["chapters"])
    _save_project(pid, proj)
    return jsonify({"id": pid, **proj})

@app.route("/api/projects/<pid>/chapters", methods=["POST"])
def add_chapter(pid):
    proj = _load_project(pid)
    if not proj: return jsonify({"error": "not found"}), 404
    d = request.get_json()
    title = d.get("title", f"第{len(proj.get('chapters',[]))+1}章")
    text = d.get("text", "")
    chap = {"title": title, "text": text, "chars": len(text),
            "imported_at": datetime.now().isoformat(timespec="seconds")}
    proj.setdefault("chapters", []).append(chap)
    proj["total_chars"] = sum(c["chars"] for c in proj["chapters"])
    _save_project(pid, proj)
    return jsonify({"id": pid, **proj})

@app.route("/api/projects/<pid>/chapters/<int:idx>/segments", methods=["GET"])
def get_chapter_segments(pid, idx):
    """获取某章的脚本分段（用于编辑页面）。"""
    proj = _load_project(pid, load_chapters=True)
    if not proj: return jsonify({"error": "not found"}), 404
    chs = proj.get("chapters", [])
    if idx < 0 or idx >= len(chs): return jsonify({"error": "invalid index"}), 400
    ch = chs[idx]
    segs = ch.get("_segments", [])
    if not segs and proj.get("characters"):
        segs = parse_script_regex(ch["text"], proj["characters"])
    return jsonify({"title": ch["title"], "segments": segs})

@app.route("/api/projects/<pid>/chapters/<int:idx>/segments", methods=["PUT"])
def save_chapter_segments(pid, idx):
    """保存某章的脚本分段修改。"""
    proj = _load_project(pid, load_chapters=True)
    if not proj: return jsonify({"error": "not found"}), 404
    chs = proj.get("chapters", [])
    if idx < 0 or idx >= len(chs): return jsonify({"error": "invalid index"}), 400
    d = request.get_json(silent=True) or {}
    chs[idx]["_segments"] = d.get("segments", [])
    _save_chapter(pid, idx, chs[idx])
    return jsonify({"ok": True})

@app.route("/api/projects/<pid>/save-chapters", methods=["POST"])
def save_chapters(pid):
    """保存章节的 _selected 等元数据。"""
    d = request.get_json(silent=True) or {}
    chapters = d.get("chapters", [])
    for i, ch in enumerate(chapters):
        _save_chapter(pid, i, ch)
    return jsonify({"ok": True})

@app.route("/api/projects/<pid>/chapters/<int:idx>", methods=["DELETE"])
def delete_chapter(pid, idx):
    proj = _load_project(pid)
    if not proj: return jsonify({"error": "not found"}), 404
    chs = proj.get("chapters", [])
    if 0 <= idx < len(chs):
        chs.pop(idx)
        proj["total_chars"] = sum(c["chars"] for c in chs)
        _save_project(pid, proj)
    return jsonify({"id": pid, **proj})

# ── 角色卡 ──
def _sample_text(texts, max_chars):
    """从多章文本中采样，不超过 max_chars。取前 1/3 + 中 1/3 + 后 1/3。"""
    full = "\n\n".join(texts)
    if len(full) <= max_chars:
        return full
    third = max_chars // 3
    return full[:third] + "\n...\n" + full[len(full)//2 - third//2:len(full)//2 + third//2] + "\n...\n" + full[-third:]

def _process_llm_batch(batch_texts, batch_indices, chapters, all_chars, all_segments, key, mode):
    """阶段一：角色识别（多章合并，只提取角色）。阶段二：逐章 LLM 划分。"""
    combined = "\n\n".join(batch_texts)
    total_tok = 0
    # 阶段一：角色识别（输出小巧，JSON 可靠）
    result = detect_characters(combined, api_key=key, use_token_plan=(mode == "tokenplan"))
    new_chars = result.get("characters", [])
    total_tok += result.get("_usage", {}).get("total_tokens", 0)
    for c in new_chars:
        if c["name"] not in {x["name"] for x in all_chars}:
            all_chars.append(c)
    # 阶段二：逐章 LLM 划分（每章独立，带角色表）
    from tts_audiobook.script_parser import parse_script_llm
    for bi, bt in zip(batch_indices, batch_texts):
        try:
            segs, llm_u = parse_script_llm(bt, all_chars, api_key=key,
                use_token_plan=(mode == "tokenplan"))
            chapters[bi]["_segments"] = segs
            chapters[bi]["_parsed_done"] = True
            all_segments.extend(segs)
            total_tok += llm_u.get("total_tokens", 0)
        except Exception as e:
            print(f"[detect-chars] LLM parse failed for ch{bi}, using regex: {e}")
            segs = parse_script_regex(bt, all_chars)
            chapters[bi]["_segments"] = segs
            chapters[bi]["_parsed_done"] = True
            all_segments.extend(segs)
    print(f"[detect-chars] Batch {batch_indices}: {len(all_chars)} chars total, {total_tok} LLM tokens")
    return total_tok

def _assign_segs_to_chapters(segs, batch_texts, batch_indices, chapters):
    """将合并的 segments 按原文匹配分回各章节。"""
    combined = "\n\n".join(batch_texts)
    pos = 0
    for bi, bt in zip(batch_indices, batch_texts):
        ch_segs = []
        end_pos = pos + len(bt)
        for s in segs:
            s_pos = combined.find(s["text"], pos, end_pos + 50)
            if s_pos >= 0 and s_pos < end_pos:
                ch_segs.append(s)
        chapters[bi]["_segments"] = ch_segs
        chapters[bi]["_parsed_done"] = True
        pos = end_pos + 2  # skip 



@app.route("/api/projects/<pid>/detect-chars", methods=["POST"])
def detect_project_chars(pid):
    d = request.get_json(silent=True) or {}
    mode = d.get("mode", "normal")
    text = d.get("text", "")
    with_parse = d.get("with_parse", False)
    force = d.get("force", False)
    llm_cfg = d.get("llm_config", {})  # 用户自定义 LLM 配置
    proj = _load_project(pid)
    if force:
        for ch in proj.get("chapters", []):
            ch.pop("_parsed_done", None)
            ch.pop("_segments", None)
        proj["characters"] = []  # 清空角色卡，从头识别
        _save_project(pid, proj)
        print("[detect-chars] Force re-detect: cleared all flags + characters")
    if not proj: return jsonify({"error": "项目不存在"}), 404
    if not text:
        text = "\n\n".join(c["text"] for c in proj.get("chapters", []))
    if not text: return jsonify({"error": "请先添加章节"}), 400
    key = _load_api_key(mode)
    if not key:
        return jsonify({"error": f"请先配置 API Key (当前模式: {mode})"}), 400
    try:
        print(f"[detect-chars] pid={pid} mode={mode} text_len={len(text)} model={'mimo-v2.5' if mode=='tokenplan' else 'mimo-v2-flash'}")
        # 仅处理勾选的章节（默认全部勾选）
        chapters = [c for c in proj.get("chapters", []) if c.get("_selected", True) != False]
        if not chapters:
            return jsonify({"error": "没有勾选任何章节"}), 400
        all_chars = list(proj.get("characters", []))
        all_segments = []
        total_llm = 0

        last_result = {}
        if with_parse:
            # 阶段一：尽可能多章合并，提取全部角色
            CHAR_BATCH = 120000  # 角色提取用大 batch
            batch_texts = []
            for i, ch in enumerate(chapters):
                if ch.get("_parsed_done"):
                    all_segments.extend(ch.get("_segments", []))
                    continue
                batch_texts.append(ch["text"])
                if sum(len(t) for t in batch_texts) >= CHAR_BATCH or i == len(chapters) - 1:
                    combined = "\n\n".join(batch_texts)
                    result = detect_characters(combined, api_key=key, use_token_plan=(mode == "tokenplan"))
                    for c in result.get("characters", []):
                        if c["name"] not in {x["name"] for x in all_chars}:
                            all_chars.append(c)
                    total_llm += result.get("_usage", {}).get("total_tokens", 0)
                    # 保存旁白信息
                    if result.get("narrator_voice"):
                        last_result["narrator_voice"] = result["narrator_voice"]
                    if result.get("narrator_style"):
                        last_result["narrator_style"] = result["narrator_style"]
                    print(f"[detect-chars] Chars batch: {len(all_chars)} chars after {len(batch_texts)} chapters")
                    batch_texts = []
            # 阶段一结束，立即保存角色 + 旁白信息
            proj["characters"] = all_chars
            if last_result.get("narrator_voice"):
                proj["narrator_voice"] = last_result["narrator_voice"]
            if last_result.get("narrator_style"):
                proj["narrator_style"] = last_result["narrator_style"]
            _save_project(pid, proj)
            print(f"[detect-chars] Stage 1 done: {len(all_chars)} characters, narrator={proj.get('narrator_voice')}, saved")
            # 阶段二：逐章 LLM 划分（每章独立，带角色表）
            from tts_audiobook.script_parser import parse_script_llm
            for i, ch in enumerate(chapters):
                if ch.get("_parsed_done"):
                    continue
                try:
                    segs, llm_u = parse_script_llm(ch["text"], all_chars, api_key=key,
                        use_token_plan=(mode == "tokenplan"))
                    ch["_segments"] = segs
                    total_llm += llm_u.get("total_tokens", 0)
                except Exception as e:
                    print(f"[detect-chars] LLM parse failed ch{i}, regex fallback: {e}")
                    ch["_segments"] = parse_script_regex(ch["text"], all_chars)
                ch["_parsed_done"] = True
                all_segments.extend(ch["_segments"])
                _save_chapter(pid, i, ch)  # 每章立即存盘
                print(f"[detect-chars] Ch{i+1}/{len(chapters)}: {len(ch['_segments'])} segs saved, {total_llm} LLM tokens")
            chars = all_chars
            segments = all_segments
        else:
            CHARS_BATCH = 120000
            batch_texts = []
            for i, ch in enumerate(chapters):
                if ch.get("_parsed_done"):
                    all_segments.extend(ch.get("_segments", []))
                    continue
                batch_texts.append(ch["text"])
                if sum(len(t) for t in batch_texts) >= CHARS_BATCH or i == len(chapters) - 1:
                    combined = "\n\n".join(batch_texts)
                    last_result = detect_characters(combined, api_key=key, use_token_plan=(mode == "tokenplan"))
                    new_chars = last_result.get("characters", [])
                    for c in new_chars:
                        if c["name"] not in {x["name"] for x in all_chars}:
                            all_chars.append(c)
                    total_llm += last_result.get("_usage", {}).get("total_tokens", 0)
                    print(f"[detect-chars] Batch {len(all_chars)} chars after processing {len(batch_texts)} chapters")
                    batch_texts = []
            proj["characters"] = all_chars
            if last_result.get("narrator_voice"):
                proj["narrator_voice"] = last_result["narrator_voice"]
            if last_result.get("narrator_style"):
                proj["narrator_style"] = last_result["narrator_style"]
            _save_project(pid, proj)
            for i, ch in enumerate(chapters):
                if ch.get("_parsed_done"):
                    continue
                ch_segs = parse_script_regex(ch["text"], all_chars)
                ch["_segments"] = ch_segs
                ch["_parsed_done"] = True
                all_segments.extend(ch_segs)
            chars = all_chars
            segments = all_segments
        old_map = {c["name"]: c.get("history", []) for c in proj.get("characters", [])}
        for c in chars:
            c["history"] = old_map.get(c["name"], [])
        nv = last_result.get("narrator_voice", NARRATOR_VOICE) or proj.get("narrator_voice", NARRATOR_VOICE)
        ns = last_result.get("narrator_style", "") or proj.get("narrator_style", "")
        proj["characters"] = chars
        if nv: proj["narrator_voice"] = nv
        if ns: proj["narrator_style"] = ns
        proj["llm_tokens"] = (proj.get("llm_tokens", 0) + total_llm)
        _save_project(pid, proj)
        print(f"[detect-chars] Done: {len(chars)} chars + {len(segments)} segments, total LLM tokens={total_llm}")
        return jsonify({"characters": chars, "narrator_voice": nv, "narrator_style": ns})
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

@app.route("/api/projects/<pid>/save-chars", methods=["POST"])
def save_project_chars(pid):
    proj = _load_project(pid)
    if not proj: return jsonify({"error": "not found"}), 404
    d = request.get_json()
    proj["characters"] = d.get("characters", [])
    proj["narrator_voice"] = d.get("narrator_voice", NARRATOR_VOICE)
    proj["narrator_style"] = d.get("narrator_style", "")
    _save_project(pid, proj)
    return jsonify({"ok": True})

# ── 音色定制 ──
@app.route("/api/save-voice-sample", methods=["POST"])
def save_voice_sample():
    """保存音色样本到文件。"""
    d = request.get_json(silent=True) or {}
    pid = d.get("pid", "")
    char_name = d.get("char_name", "unknown")
    audio_b64 = d.get("audio_b64", "")
    if not pid or not audio_b64:
        return jsonify({"ok": False, "error": "缺少参数"})
    vdir = _proj_dir(pid) / "voice_samples"
    vdir.mkdir(exist_ok=True)
    fname = re.sub(r'[\\/*?:"<>|]', '', char_name)[:20] + ".wav"
    (vdir / fname).write_bytes(base64.b64decode(audio_b64))
    return jsonify({"ok": True, "file": f"voice_samples/{fname}"})

@app.route("/api/delete-voice-sample", methods=["POST"])
def delete_voice_sample():
    d = request.get_json(silent=True) or {}
    pid = d.get("pid", "")
    file = d.get("file", "")
    if pid and file:
        p = _proj_dir(pid) / file
        if p.exists(): p.unlink()
    return jsonify({"ok": True})

@app.route("/api/list-models", methods=["POST"])
def list_models():
    """探测 LLM 端点可用的模型列表。"""
    d = request.get_json(silent=True) or {}
    url = d.get("url", "").strip()
    key = d.get("key", "").strip()
    if not url:
        url = MIMO_TOKEN_PLAN_URL if S.apiMode == "tokenplan" else MIMO_BASE_URL  # won't work, use default
        url = MIMO_BASE_URL
    if not key:
        key = _load_api_key("normal") or _load_api_key("tokenplan")
    if not key:
        return jsonify({"error": "请先配置 API Key"})
    try:
        client = OpenAI(api_key=key, base_url=url)
        models = client.models.list()
        return jsonify({"models": [m.id for m in models.data[:50]]})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/voice-design", methods=["POST"])
def voice_design():
    """用 VoiceDesign 模型生成音色样本。"""
    d = request.get_json(silent=True) or {}
    mode = d.get("mode", "normal")
    description = d.get("description", "")
    sample_text = d.get("sample_text", "这是一个音色样本，用于测试该角色的声音效果。")
    key = _load_api_key(mode)
    if not key: return jsonify({"error": "请先配置 API Key"}), 400
    if not description: return jsonify({"error": "请提供音色描述"}), 400
    try:
        use_token_plan = mode == "tokenplan"
        base_url = MIMO_TOKEN_PLAN_URL if use_token_plan else MIMO_BASE_URL
        client = OpenAI(api_key=key, base_url=base_url)
        completion = client.chat.completions.create(
            model="mimo-v2.5-tts-voicedesign",
            messages=[
                {"role": "user", "content": description},
                {"role": "assistant", "content": sample_text}
            ],
            audio={"format": "wav"},
            timeout=120,
        )
        audio_b64 = completion.choices[0].message.audio.data
        return jsonify({"audio_b64": audio_b64, "format": "wav", "sample_rate": 24000})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/voice-clone-synth", methods=["POST"])
def voice_clone_synth():
    """用 VoiceClone 模型合成语音（基于已保存的音色样本）。"""
    d = request.get_json(silent=True) or {}
    mode = d.get("mode", "normal")
    text = d.get("text", "")
    voice_sample_b64 = d.get("voice_sample", "")
    key = _load_api_key(mode)
    if not key: return jsonify({"error": "请先配置 API Key"}), 400
    if not text or not voice_sample_b64:
        return jsonify({"error": "缺少文本或音色样本"}), 400
    try:
        use_token_plan = mode == "tokenplan"
        base_url = MIMO_TOKEN_PLAN_URL if use_token_plan else MIMO_BASE_URL
        client = OpenAI(api_key=key, base_url=base_url)
        completion = client.chat.completions.create(
            model="mimo-v2.5-tts-voiceclone",
            messages=[
                {"role": "user", "content": ""},
                {"role": "assistant", "content": text}
            ],
            audio={"format": "wav", "voice": f"data:audio/wav;base64,{voice_sample_b64}"},
            timeout=120,
        )
        audio_b64 = completion.choices[0].message.audio.data
        audio_bytes = base64.b64decode(audio_b64)
        usage = {
            "prompt_tokens": completion.usage.prompt_tokens,
            "completion_tokens": completion.usage.completion_tokens,
            "total_tokens": completion.usage.total_tokens,
        }
        return jsonify({"audio_b64": audio_b64, "_usage": usage})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ── 合成 ──
@app.route("/api/projects/<pid>/synthesize", methods=["POST"])
def start_project_synth(pid):
    d = request.get_json()
    mode = d.get("mode", "normal")
    use_llm = d.get("use_llm", False)
    key = _load_api_key(mode)
    if not key: return jsonify({"error": "请先配置 API Key"}), 400
    proj = _load_project(pid)
    if not proj: return jsonify({"error": "not found"}), 404
    chs = proj.get("chapters", [])
    if not chs: return jsonify({"error": "请先添加章节"}), 400
    # 仅合成勾选的章节
    sel_chs = [c for c in chs if c.get("_selected", True) != False]
    if not sel_chs: return jsonify({"error": "没有勾选任何章节"}), 400
    text = "\n\n".join(c["text"] for c in sel_chs)

    tid = uuid.uuid4().hex[:8]
    _tasks[tid] = {"status": "running", "current": 0, "total": 0, "tokens": 0, "cost": 0.0,
                   "duration": 0.0, "is_free": True, "log": [], "file": None, "error": None, "audio_chunks": []}
    Thread(target=_run_synth, args=(tid, pid, text, proj, key, mode == "tokenplan", use_llm), daemon=True).start()
    return jsonify({"task_id": tid})

def _run_synth(tid, pid, text, proj, api_key, use_token_plan, use_llm=False):
    t = _tasks[tid]
    try:
        chars = proj.get("characters", [])
        nv = proj.get("narrator_voice", NARRATOR_VOICE)
        ns = proj.get("narrator_style", "")
        # 从章节中读取已缓存的分段
        cached_segs = []
        for ch in proj.get("chapters", []):
            if ch.get("_segments"):
                cached_segs.extend(ch["_segments"])
        if cached_segs and not use_llm:
            t["log"].append({"level": "info", "msg": "使用已缓存的分段结果"})
            segs = cached_segs
            # 刷新音色：用户可能修改了角色卡的音色分配
            char_voice_map = {c["name"]: c.get("assigned_voice", "") for c in chars}
            for s in segs:
                sp = s.get("speaker", "旁白")
                if sp in char_voice_map:
                    s["voice"] = char_voice_map[sp]
            llm_usage = None
        else:
            t["log"].append({"level": "info", "msg": "解析脚本..."})
            segs, llm_usage = parse_script(text, chars, narrator_style=ns, narrator_voice=nv,
                               use_llm=use_llm, api_key=api_key, use_token_plan=use_token_plan)
            t["_llm_usage"] = llm_usage
        t["total"] = len(segs)

        # 打印音色分配摘要
        voice_summary = {}
        for s in segs:
            v = s.get("voice", "?") or "?"
            sp = s.get("speaker", "?")
            voice_summary[f"{sp}→{v}"] = voice_summary.get(f"{sp}→{v}", 0) + 1
        for k, count in sorted(voice_summary.items(), key=lambda x: -x[1]):
            t["log"].append({"level": "info", "msg": f"  分配: {k} x{count}"})
        client = MiMoTTSClient(api_key=api_key, voice=nv, style=ns, use_token_plan=use_token_plan)
        trk = CostTracker()
        for i, seg in enumerate(segs):
            t0 = time.time()
            seg_text = seg["text"].strip()
            if seg_text and seg_text[-1] not in "。！？.!?…~～\"\"」」''":
                seg_text += "。"
            # 检查角色是否有定制音色
            speaker = seg.get("speaker", "旁白")
            char_voice_sample = None
            for c in chars:
                if c["name"] == speaker and c.get("voice_sample_file"):
                    vf = _proj_dir(pid) / c["voice_sample_file"]
                    if vf.exists():
                        char_voice_sample = base64.b64encode(vf.read_bytes()).decode()
                    break
            if char_voice_sample:
                clone_client = OpenAI(api_key=api_key,
                    base_url=MIMO_TOKEN_PLAN_URL if use_token_plan else MIMO_BASE_URL)
                try:
                    clone_comp = clone_client.chat.completions.create(
                        model="mimo-v2.5-tts-voiceclone",
                        messages=[{"role": "user", "content": ""}, {"role": "assistant", "content": seg_text}],
                        audio={"format": "wav", "voice": f"data:audio/wav;base64,{char_voice_sample}"},
                        timeout=120)
                    wav = base64.b64decode(clone_comp.choices[0].message.audio.data)
                    usage = {"prompt_tokens": clone_comp.usage.prompt_tokens,
                             "completion_tokens": clone_comp.usage.completion_tokens,
                             "total_tokens": clone_comp.usage.total_tokens}
                except Exception:
                    wav, usage = client.synthesize(seg_text, voice=seg.get("voice", nv), style=seg.get("style", ns))
            else:
                wav, usage = client.synthesize(seg_text, voice=seg.get("voice", nv), style=seg.get("style", ns))
            el = time.time() - t0
            dur = wav_duration_sec(wav)
            trk.record(i + 1, len(seg_text), usage, dur, el)
            t["audio_chunks"].append(wav)
            t.setdefault("_usages", []).append(usage)
            t["current"] = i + 1; t["tokens"] = trk.total_tokens
            t["cost"] = trk.total_would_be_cost; t["duration"] = trk.total_duration_sec
            voice_used = seg.get("voice", nv)
            speaker = seg.get("speaker", "旁白")
            t["log"].append({"level": "info",
                "msg": f"[{i+1}/{len(segs)}] {speaker} voice={voice_used}: {usage['total_tokens']}t {el:.1f}s"})
        # 按章节保存独立音频
        book_dir = STATIC_DIR / re.sub(r'[\\/*?:"<>|]', '', proj.get("book_title", "audiobook"))
        book_dir.mkdir(exist_ok=True)
        sel_chs = [c for c in proj.get("chapters", []) if c.get("_selected", True) != False]
        if len(sel_chs) == 1:
            safe = re.sub(r'[\\/*?:"<>|]', '', sel_chs[0]["title"])[:30]
            fn = f"{safe}_{tid}.wav"
        else:
            fn = f"{proj.get('book_title','audiobook')}_{tid}.wav"
        # 按章节分组保存音频
        ch_files = []
        seg_idx = 0
        for ci, ch in enumerate(proj.get("chapters", [])):
            ch_segs = ch.get("_segments", [])
            if not ch_segs:
                continue
            ch_wavs = []
            for _ in ch_segs:
                if seg_idx < len(t["audio_chunks"]):
                    ch_wavs.append(t["audio_chunks"][seg_idx])
                    seg_idx += 1
            if ch_wavs:
                ch_merged = merge_wavs(ch_wavs)
                safe = re.sub(r'[\\/*?:"<>|]', '', ch.get("title", f"ch{ci+1}"))[:30]
                fn = f"{safe}_{tid}.wav"
                (book_dir / fn).write_bytes(ch_merged)
                ch_files.append(f"{book_dir.name}/{fn}")
        # 如果没有分章，全合并
        if not ch_files:
            merged = merge_wavs(t["audio_chunks"])
            fn = f"{proj.get('book_title','audiobook')}_{tid}.wav"
            (book_dir / fn).write_bytes(merged)
            ch_files = [f"{book_dir.name}/{fn}"]
        t["files"] = ch_files
        t["file"] = ch_files[0]
        t["status"] = "done"
        t["log"].append({"level": "success", "msg": "完成!"})
        # 累积 LLM Token（脚本解析）
        proj = _load_project(pid)
        if proj:
            if t.get("_llm_usage"):
                proj["llm_tokens"] = (proj.get("llm_tokens", 0) +
                    t["_llm_usage"].get("total_tokens", 0))
                _save_project(pid, proj)
    except Exception as e:
        t["status"] = "error"; t["error"] = str(e)
        t["log"].append({"level": "error", "msg": str(e)})

@app.route("/api/task-progress/<tid>")
def task_progress(tid):
    t = _tasks.get(tid, {})
    return jsonify({k: t.get(k) for k in ["status","current","total","tokens","cost","duration","is_free","log","file","files","error"]})

@app.route("/api/download/<path:fn>")
def download_file(fn):
    p = STATIC_DIR / fn
    return send_file(p, as_attachment=True, download_name=os.path.basename(fn)) if p.exists() else ("Not found", 404)

if __name__ == "__main__":
    print("MiMo TTS 有声书 (项目制 / 分章节 / 角色卡持久化)")
    print("访问: http://localhost:5000")
    host = os.environ.get("FLASK_HOST", "0.0.0.0")
    port = int(os.environ.get("FLASK_PORT", "5000"))
    debug = os.environ.get("FLASK_DEBUG", "0") == "1"
    app.run(host=host, port=port, debug=debug)
