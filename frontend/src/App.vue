<script setup>
import { computed, onMounted, ref } from 'vue'

const productionTabs = [
  { id: 'import', label: '1 导入章节' },
  { id: 'chapters', label: '2 章节管理' },
  { id: 'synth', label: '3 合成' }
]
const editTabs = [
  { id: 'characters', label: '角色卡' },
  { id: 'segments', label: '划分编辑' }
]

const activeTab = ref('import')
const bookTitle = ref('未命名有声书')
const bookText = ref('')
const mode = ref('tokenplan')
const mimoKey = ref('')
const llmKey = ref('')
const llm = ref({ provider: 'deepseek', url: 'https://api.deepseek.com/v1', model: 'deepseek-v4-flash', thinking: 'enabled', has_key: false, key_masked: '' })
const extractCharacters = ref(true)
const parseMode = ref('llm')
const characterScope = ref('selected')
const characterBatchChars = ref(120000)
const useCloneLibrary = ref(true)
const configOpen = ref(false)
const workflow = ref([])
const voices = ref([])
const projects = ref([])
const activeProjectId = ref('')
const activeProject = ref(null)
const chapters = ref([])
const selectedChapter = ref(0)
const segmentDraft = ref({ title: '', text: '', segments: [] })
const taskId = ref('')
const task = ref(null)
const busy = ref(false)
const error = ref('')
const probe = ref(null)
const draggingSegment = ref(null)
let dragScrollTimer = null
let timer = null

const progress = computed(() => task.value?.total ? Math.round((task.value.current / task.value.total) * 100) : task.value?.status === 'done' ? 100 : 0)
const stageLabel = computed(() => ({ queued: '排队', prepare: '准备', detect: '识别角色', parse: '划分脚本', voice_cast: '挑选音色', synthesize: '合成音频', done: '完成', error: '出错' }[task.value?.stage] || '待开始'))
const presetVoices = [
  { id: '冰糖', name: '冰糖', gender: '女', age: '青年', scene: '通用', style: '甜美、活泼、明亮', is_preset: true },
  { id: '茉莉', name: '茉莉', gender: '女', age: '中年', scene: '通用', style: '温柔、成熟、知性', is_preset: true },
  { id: '苏打', name: '苏打', gender: '男', age: '青年', scene: '通用', style: '清朗、自然、阳光', is_preset: true },
  { id: '白桦', name: '白桦', gender: '男', age: '中年', scene: '通用', style: '沉稳、磁性、厚重', is_preset: true },
]
const allVoices = computed(() => [...voices.value, ...presetVoices])
const voiceById = computed(() => Object.fromEntries(allVoices.value.map(voice => [voice.id, voice])))
const speakerOptions = computed(() => ['旁白', ...(activeProject.value?.characters || []).map(c => c.name)])
const selectedProjectTitle = computed(() => activeProject.value?.book_title || '未选择项目')

async function request(url, options) {
  const response = await fetch(url, options)
  const text = await response.text()
  const data = text ? JSON.parse(text) : {}
  if (!response.ok || data.error) throw new Error(data.error || `HTTP ${response.status}`)
  return data
}

async function load() {
  error.value = ''
  const [workflowData, voicesData, llmData, projectsData] = await Promise.all([
    request('/api/workflow'),
    request('/api/builtin-voices'),
    request('/api/llm-config'),
    request('/api/projects')
  ])
  workflow.value = workflowData.steps
  voices.value = voicesData.voices
  projects.value = projectsData
  llm.value = { ...llm.value, ...llmData }
  if (activeProjectId.value) await selectProject(activeProjectId.value, false)
}

async function selectProject(projectId, switchTab = true) {
  if (!projectId) return
  activeProjectId.value = projectId
  activeProject.value = await request(`/api/projects/${projectId}`)
  const data = await request(`/api/projects/${projectId}/chapters`)
  chapters.value = data.chapters
  if (chapters.value.length) {
    selectedChapter.value = chapters.value[0].index
    await loadSegments(selectedChapter.value)
  } else {
    segmentDraft.value = { title: '', text: '', segments: [] }
  }
  if (switchTab) activeTab.value = chapters.value.length ? 'chapters' : 'import'
  const tid = activeProject.value?._task_id
  if (tid) { startPolling(tid); busy.value = true; activeTab.value = 'synth' }
}

async function createProject() {
  error.value = ''
  try {
    const project = await request('/api/projects', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ book_title: bookTitle.value || '未命名有声书' })
    })
    await load()
    await selectProject(project.id)
    activeTab.value = 'import'
  } catch (err) {
    error.value = err.message
  }
}

async function deleteActiveProject() {
  if (!activeProjectId.value) return
  const title = activeProject.value?.book_title || '当前项目'
  if (!confirm(`确定删除「${title}」？项目数据和生成音频会一起删除。`)) return
  error.value = ''
  try {
    await request(`/api/projects/${activeProjectId.value}`, { method: 'DELETE' })
    activeProjectId.value = ''
    activeProject.value = null
    chapters.value = []
    segmentDraft.value = { title: '', text: '', segments: [] }
    await load()
    activeTab.value = 'import'
  } catch (err) {
    error.value = err.message
  }
}

async function importToProject(goGenerate = false) {
  if (!activeProjectId.value) {
    await createProject()
  }
  if (!bookText.value.trim()) {
    error.value = '请先粘贴或选择 txt/md 正文'
    return
  }
  busy.value = true
  error.value = ''
  try {
    await request(`/api/projects/${activeProjectId.value}/import-book`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text: bookText.value })
    })
    await load()
    await selectProject(activeProjectId.value, false)
    activeTab.value = goGenerate ? 'synth' : 'characters'
    if (goGenerate) await regenerateProject()
  } catch (err) {
    error.value = err.message
  } finally {
    if (!goGenerate) busy.value = false
  }
}

async function readTextFile(event) {
  const file = event.target.files?.[0]
  if (!file) return
  bookText.value = await file.text()
  if (!bookTitle.value || bookTitle.value === '未命名有声书') {
    bookTitle.value = file.name.replace(/\.(txt|md)$/i, '')
  }
}

async function loadSegments(index) {
  if (!activeProjectId.value) return
  selectedChapter.value = index
  segmentDraft.value = await request(`/api/projects/${activeProjectId.value}/chapters/${index}/segments`)
}

async function saveMimoKey() {
  if (!mimoKey.value.trim()) return
  await request('/api/set-key', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ key: mimoKey.value.trim(), mode: mode.value }) })
  mimoKey.value = ''
}

async function saveLLM() {
  const data = await request('/api/llm-config', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ key: llmKey.value.trim(), provider: llm.value.provider, url: llm.value.url, model: llm.value.model, thinking: llm.value.thinking })
  })
  llmKey.value = ''
  llm.value = { ...llm.value, ...data }
}

async function regenerateProject() {
  if (!activeProjectId.value) {
    error.value = '请先创建或选择项目'
    return
  }
  busy.value = true
  error.value = ''
  try {
    if (!mimoKey.value.trim()) {
      const keyStatus = await request(`/api/key-status?mode=${mode.value}`)
      if (!keyStatus.has_key) {
        busy.value = false
        error.value = '未配置 MiMo API Key，请在连接配置中填入 Key 或前往 platform.xiaomimimo.com 获取'
        return
      }
    }
    await saveMimoKey()
    await saveLLM()
    const data = await request(`/api/projects/${activeProjectId.value}/one-click-generate`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        mode: mode.value,
        cast_voices: true,
        use_clone_library: useCloneLibrary.value,
        extract_characters: extractCharacters.value,
        parse_mode: parseMode.value,
        character_scope: characterScope.value,
        character_batch_chars: Number(characterBatchChars.value) || 120000
      })
    })
    activeTab.value = 'synth'
    startPolling(data.task_id)
  } catch (err) {
    busy.value = false
    error.value = err.message
  }
}

function startPolling(id) {
  taskId.value = id
  pollTask()
  clearInterval(timer)
  timer = setInterval(pollTask, 1600)
}

async function pollTask() {
  if (!taskId.value) return
  try {
    task.value = await request(`/api/task-progress/${taskId.value}`)
    if (['done', 'error'].includes(task.value.status)) {
      busy.value = false
      clearInterval(timer)
      await load()
      if (task.value.project_id) await selectProject(task.value.project_id, false)
    }
  } catch (err) {
    busy.value = false
    error.value = err.message
    clearInterval(timer)
  }
}

async function toggleChapter(chapter) {
  if (!activeProjectId.value) return
  chapter.selected = !chapter.selected
  await request(`/api/projects/${activeProjectId.value}/chapters/${chapter.index}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ selected: chapter.selected })
  })
}

const allChaptersSelected = computed(() => chapters.value.length > 0 && chapters.value.every(ch => ch.selected))

async function toggleAllChapters() {
  if (!activeProjectId.value || !chapters.value.length) return
  const newState = !allChaptersSelected.value
  await request(`/api/projects/${activeProjectId.value}/chapters/bulk-select`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ selected: newState })
  })
  for (const chapter of chapters.value) chapter.selected = newState
}

async function deleteChapter(chapter) {
  if (!activeProjectId.value) return
  if (!confirm(`确定删除章节「${chapter.title}」？`)) return
  error.value = ''
  try {
    await request(`/api/projects/${activeProjectId.value}/chapters/${chapter.index}`, { method: 'DELETE' })
    await selectProject(activeProjectId.value, false)
  } catch (err) {
    error.value = err.message
  }
}


async function redetectCharacters() {
  if (!activeProjectId.value) return
  busy.value = true
  error.value = ''
  try {
    await saveMimoKey()
    const project = await request(`/api/projects/${activeProjectId.value}/detect-characters`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ mode: mode.value, use_clone_library: useCloneLibrary.value, character_batch_chars: Number(characterBatchChars.value) || 120000 })
    })
    activeProject.value = project
    busy.value = false
  } catch (err) { busy.value = false; error.value = err.message }
}

async function reparseCurrentChapter() {
  if (!activeProjectId.value) return
  busy.value = true
  error.value = ''
  try {
    await saveMimoKey()
    const result = await request(`/api/projects/${activeProjectId.value}/chapters/${selectedChapter.value}/reparse`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ mode: mode.value })
    })
    segmentDraft.value = result
    busy.value = false
  } catch (err) { busy.value = false; error.value = err.message }
}

function addCharacter() {
  activeProject.value.characters ||= []
  activeProject.value.characters.push({ name: '新角色', aliases: [], gender: '男', age: '青年', role: '', personality: '', speaking_style: '', assigned_voice: '苏打', builtin_voice_id: voices.value[0]?.id || '' })
}

function removeCharacter(index) {
  activeProject.value.characters.splice(index, 1)
}

async function saveCharacters() {
  if (!activeProject.value) return
  const characters = (activeProject.value.characters || []).map(character => ({
    ...character,
    aliases: Array.isArray(character.aliases)
      ? character.aliases
      : String(character.aliases || '').split(/[,，]/).map(item => item.trim()).filter(Boolean)
  }))
  activeProject.value = await request(`/api/projects/${activeProject.value.id}/characters`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      characters,
      narrator_voice: activeProject.value.narrator_voice,
      narrator_style: activeProject.value.narrator_style,
      narrator_builtin_voice_id: activeProject.value.narrator_builtin_voice_id
    })
  })
  await load()
}

function addSegment() {
  segmentDraft.value.segments.push({ speaker: '旁白', text: '', voice: activeProject.value?.narrator_voice || '', style: activeProject.value?.narrator_style || '' })
}

function removeSegment(index) {
  segmentDraft.value.segments.splice(index, 1)
}

function moveSegment(index, direction) {
  const target = index + direction
  if (target < 0 || target >= segmentDraft.value.segments.length) return
  const [item] = segmentDraft.value.segments.splice(index, 1)
  segmentDraft.value.segments.splice(target, 0, item)
}

function dragSegmentStart(index) {
  draggingSegment.value = index
}

function dropSegment(targetIndex) {
  const sourceIndex = draggingSegment.value
  stopDragAutoScroll()
  if (sourceIndex === null || sourceIndex === targetIndex) return
  const [item] = segmentDraft.value.segments.splice(sourceIndex, 1)
  segmentDraft.value.segments.splice(targetIndex, 0, item)
}

function stopDragAutoScroll() {
  draggingSegment.value = null
  clearInterval(dragScrollTimer)
  dragScrollTimer = null
}

function handleSegmentDragOver(event) {
  event.preventDefault()
  const margin = 90
  const speed = 18
  const y = event.clientY
  const viewportHeight = window.innerHeight
  let direction = 0
  if (y < margin) direction = -1
  if (y > viewportHeight - margin) direction = 1

  if (!direction) {
    clearInterval(dragScrollTimer)
    dragScrollTimer = null
    return
  }
  if (dragScrollTimer) return
  dragScrollTimer = setInterval(() => window.scrollBy({ top: direction * speed, behavior: 'auto' }), 16)
}

async function saveSegments() {
  if (!activeProjectId.value) return
  await request(`/api/projects/${activeProjectId.value}/chapters/${selectedChapter.value}/segments`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ segments: segmentDraft.value.segments })
  })
  await selectProject(activeProjectId.value, false)
}

async function runProbe(provider = 'deepseek') {
  error.value = ''
  probe.value = null
  try {
    probe.value = await request('/api/llm-json-probe', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ provider, url: llm.value.url, model: llm.value.model, thinking: llm.value.thinking }) })
  } catch (err) {
    error.value = err.message
  }
}

function useDeepSeekPreset() {
  llm.value.provider = 'deepseek'
  llm.value.url = 'https://api.deepseek.com/v1'
  llm.value.model = 'deepseek-v4-flash'
  llm.value.thinking = 'enabled'
}

function useMimoPreset() {
  llm.value.provider = 'mimo'
  llm.value.url = 'https://api.xiaomimimo.com/v1'
  llm.value.model = 'mimo-v2.5'
  llm.value.thinking = 'disabled'
}

function applyProviderPreset() {
  if (llm.value.provider === 'deepseek') useDeepSeekPreset()
  else useMimoPreset()
}

onMounted(load)
</script>

<template>
  <main class="studio">
    <header class="app-header">
      <div>
        <p class="eyebrow">MiMo TTS</p>
        <h1>有声书项目工作台</h1>
      </div>
      <div class="header-actions">
        <button class="ghost compact" @click="configOpen = !configOpen">{{ configOpen ? '收起配置' : '连接配置' }}</button>
      </div>
    </header>

    <p v-if="error" class="notice error">{{ error }}</p>

    <section v-if="configOpen" class="panel config-panel">
      <div class="panel-title">
        <h2>连接配置</h2>
        <span>MiMo TTS 和 LLM 分开配置</span>
      </div>
      <div class="config-grid">
        <section class="config-block">
          <h3>MiMo TTS / 克隆合成</h3>
          <div class="settings-grid">
            <div>
              <label>API 类型</label>
              <select v-model="mode">
                <option value="tokenplan">Token Plan 专属 API</option>
                <option value="normal">按量 API</option>
              </select>
            </div>
            <div>
              <label>MiMo Key</label>
              <input v-model="mimoKey" type="password" placeholder="留空则使用后端已保存 Key" />
            </div>
          </div>
          <button type="button" class="ghost compact" @click="saveMimoKey">保存 MiMo Key</button>
        </section>

        <section class="config-block">
          <h3>LLM 识别 / 分段 / 选声</h3>
          <div class="settings-grid">
            <div>
              <label>LLM 服务</label>
              <select v-model="llm.provider" @change="applyProviderPreset">
                <option value="deepseek">DeepSeek</option>
                <option value="mimo">MiMo</option>
              </select>
            </div>
            <div>
              <label>LLM Key</label>
              <input v-model="llmKey" type="password" :placeholder="llm.has_key ? `已保存 ${llm.key_masked}` : '输入当前 LLM Key'" />
            </div>
            <div>
              <label>Base URL</label>
              <input v-model="llm.url" />
            </div>
            <div>
              <label>模型</label>
              <input v-model="llm.model" />
            </div>
            <div>
              <label>思考模式</label>
              <select v-model="llm.thinking">
                <option value="enabled">开启</option>
                <option value="disabled">关闭</option>
              </select>
            </div>
          </div>
          <div class="pill-row">
            <button type="button" class="ghost compact" @click="saveLLM">保存 LLM 配置</button>
            <button type="button" class="ghost compact" @click="runProbe(llm.provider)">测试 JSON</button>
          </div>
          <pre v-if="probe" class="probe">{{ JSON.stringify(probe, null, 2) }}</pre>
        </section>
      </div>
    </section>

    <section class="workbench">
      <aside class="panel sidebar">
        <div class="panel-title">
          <h2>项目</h2>
          <span>{{ projects.length }}</span>
        </div>
        <input v-model="bookTitle" placeholder="新项目书名" />
        <div class="sidebar-actions">
          <button class="primary compact" @click="createProject">新建项目</button>
          <button class="danger compact" :disabled="!activeProjectId" @click="deleteActiveProject">删除项目</button>
        </div>
        <div class="project-list">
          <button
            v-for="project in projects"
            :key="project.id"
            :class="['project-card', { active: project.id === activeProjectId }]"
            @click="selectProject(project.id)"
          >
            <strong>{{ project.book_title }}</strong>
            <span>{{ project.chapter_count || 0 }} 章 · {{ (project.total_chars || 0).toLocaleString() }} 字</span>
          </button>
        </div>
        <nav class="side-steps">
          <strong>生产流程</strong>
          <button v-for="tab in productionTabs" :key="tab.id" :class="{ active: activeTab === tab.id }" @click="activeTab = tab.id">
            {{ tab.label }}
          </button>
          <strong>编辑区</strong>
          <button v-for="tab in editTabs" :key="tab.id" :class="{ active: activeTab === tab.id }" @click="activeTab = tab.id">
            {{ tab.label }}
          </button>
        </nav>
      </aside>

      <section class="panel main-panel">
        <div v-if="activeTab === 'import'" class="tab-content">
          <div class="panel-title">
            <h2>导入章节</h2>
            <span>{{ bookText.length.toLocaleString() }} 字</span>
          </div>
          <input type="file" accept=".txt,.md,text/plain,text/markdown" @change="readTextFile" />
          <textarea v-model="bookText" placeholder="粘贴 txt/md 正文，或选择文件。导入后会自动分章。" />
          <div class="action-grid">
            <button class="ghost" :disabled="!bookText.trim() || busy" @click="importToProject(false)">导入到项目</button>
            <button class="primary" :disabled="!bookText.trim() || busy" @click="importToProject(true)">导入并生成</button>
          </div>
          <div v-if="chapters.length" class="chapter-list">
            <article v-for="chapter in chapters" :key="chapter.index">
              <label class="check-line">
                <input type="checkbox" :checked="chapter.selected" @change="toggleChapter(chapter)" />
                <span>{{ chapter.index + 1 }}. {{ chapter.title }}</span>
              </label>
              <small>{{ chapter.chars.toLocaleString() }} 字 · {{ chapter.parsed ? '已划分' : '未划分' }}</small>
              <button class="danger icon-small" @click="deleteChapter(chapter)">删除</button>
            </article>
          </div>
        </div>

        <div v-if="activeTab === 'chapters'" class="tab-content">
          <div class="panel-title">
            <h2>章节管理</h2>
            <span>{{ selectedProjectTitle }}</span>
          </div>
          <button v-if="chapters.length" class="ghost compact" @click="toggleAllChapters">{{ allChaptersSelected ? '取消全选' : '全选' }}</button>
          <p v-if="!chapters.length" class="hint">当前项目还没有章节，请先导入书籍。</p>
          <div v-else class="chapter-list">
            <article v-for="chapter in chapters" :key="chapter.index">
              <label class="check-line">
                <input type="checkbox" :checked="chapter.selected" @change="toggleChapter(chapter)" />
                <span>{{ chapter.index + 1 }}. {{ chapter.title }}</span>
              </label>
              <small>{{ chapter.chars.toLocaleString() }} 字 · {{ chapter.parsed ? '已划分' : '未划分' }}</small>
              <button class="danger icon-small" @click="deleteChapter(chapter)">删除</button>
            </article>
          </div>
          <div class="page-actions">
            <button class="ghost" @click="activeTab = 'characters'">下一步：角色卡</button>
          </div>
        </div>

        <div v-if="activeTab === 'characters'" class="tab-content">
          <div class="section-head">
            <h2>角色卡与音色</h2>
            <div class="section-actions">
              <button class="ghost compact" :disabled="!activeProject" @click="addCharacter">添加角色</button>
              <button class="primary compact" :disabled="!activeProject || busy" @click="redetectCharacters">{{ busy ? '识别中...' : 'LLM 提取角色' }}</button>
            </div>
          </div>
          <p v-if="!activeProject" class="hint">请先选择项目。</p>
          <template v-else>
            <label>旁白朗读风格</label>
            <textarea v-model="activeProject.narrator_style" placeholder="旁白朗读风格 — 用于合成时控制语速、语调。如：沉稳大气的男声，武侠叙事感，语速适中" style="min-height:60px" />
            <label>旁白音色 <span class="help-tip" title="手动指定旁白音色。留空则由 LLM 自动匹配。克隆音色可试听。">?</span></label>
            <select v-model="activeProject.narrator_builtin_voice_id">
              <option value="">自动选择（LLM 匹配）</option>
              <optgroup label="── 克隆音色（可试听）──">
                <option v-for="voice in voices" :key="voice.id" :value="voice.id">{{ voice.name }} · {{ voice.scene }}</option>
              </optgroup>
              <optgroup label="── 预置音色 ──">
                <option v-for="voice in presetVoices" :key="voice.id" :value="voice.id">{{ voice.name }} · {{ voice.style }}</option>
              </optgroup>
            </select>
            <audio v-if="voiceById[activeProject.narrator_builtin_voice_id]?.audio_url" controls preload="none" :src="voiceById[activeProject.narrator_builtin_voice_id].audio_url"></audio>

            <article v-for="(character, index) in activeProject.characters" :key="index" class="character-card">
              <div class="character-top">
                <input v-model="character.name" placeholder="角色名（正式称呼，如：萧炎）" />
                <button class="danger icon-small" @click="removeCharacter(index)">删除</button>
              </div>
              <div class="mini-grid">
                <input v-model="character.gender" placeholder="性别（男 / 女）" />
                <input v-model="character.age" placeholder="年龄段（少年 / 青年 / 中年 / 老年 / 儿童）" />
                <input v-model="character.role" placeholder="身份（主角 / 反派 / 配角）" />
                <input v-model="character.aliases" placeholder="别称，逗号分隔（用于划分时匹配说话人）" />
              </div>
              <textarea v-model="character.speaking_style" placeholder="朗读风格 — 用于合成时控制语速、语调、音色质感。如：语速缓慢声音低沉有磁性" />
              <label>音色 <span class="help-tip" title="手动指定该角色的合成音色。留空则由 LLM 自动匹配。克隆音色可试听。">?</span></label>
              <select v-model="character.builtin_voice_id">
                <option value="">自动选择（LLM 匹配）</option>
                <optgroup label="── 克隆音色（可试听）──">
                  <option v-for="voice in voices" :key="voice.id" :value="voice.id">{{ voice.name }} · {{ voice.gender }} · {{ voice.age }} · {{ voice.scene }}</option>
                </optgroup>
                <optgroup label="── 预置音色 ──">
                  <option v-for="voice in presetVoices" :key="voice.id" :value="voice.id">{{ voice.name }} · {{ voice.style }}</option>
                </optgroup>
              </select>
              <audio v-if="voiceById[character.builtin_voice_id]?.audio_url" controls preload="none" :src="voiceById[character.builtin_voice_id].audio_url"></audio>
            </article>
            <div class="page-actions">
              <button class="primary compact" @click="saveCharacters">保存角色卡</button>
              <button class="ghost" @click="activeTab = 'segments'">下一步：划分编辑</button>
            </div>
          </template>
        </div>

        <div v-if="activeTab === 'segments'" class="tab-content">
          <div class="section-head">
            <h2>划分编辑</h2>
            <div class="section-actions">
              <button class="ghost compact" :disabled="!segmentDraft.segments" @click="addSegment">添加分段</button>
              <button class="primary compact" :disabled="!activeProjectId || busy" @click="reparseCurrentChapter">{{ busy ? '划分中...' : 'LLM 重新划分本章' }}</button>
            </div>
          </div>
          <p v-if="!chapters.length" class="hint">导入并识别后可编辑分段。</p>
          <template v-else>
            <div class="chapter-tabs">
              <button v-for="chapter in chapters" :key="chapter.index" :class="{ active: chapter.index === selectedChapter }" @click="loadSegments(chapter.index)">
                {{ chapter.index + 1 }}. {{ chapter.title }}
              </button>
            </div>
            <label>章节原文</label>
            <textarea v-model="segmentDraft.text" readonly />
            <article
              v-for="(segment, index) in segmentDraft.segments"
              :key="index"
              :class="['segment-row', { dragging: draggingSegment === index }]"
              draggable="true"
              @dragstart="dragSegmentStart(index)"
              @dragover="handleSegmentDragOver"
              @drop="dropSegment(index)"
              @dragend="stopDragAutoScroll"
            >
              <span class="drag-handle">拖动</span>
              <select v-model="segment.speaker">
                <option v-for="speaker in speakerOptions" :key="speaker" :value="speaker">{{ speaker }}</option>
              </select>
            <textarea v-model="segment.text" placeholder="分段文本" />
            <div class="segment-actions">
              <button class="ghost" :disabled="index === 0" @click="moveSegment(index, -1)">上移</button>
              <button class="ghost" :disabled="index === segmentDraft.segments.length - 1" @click="moveSegment(index, 1)">下移</button>
              <button class="danger icon-small" @click="removeSegment(index)">删除</button>
            </div>
          </article>
            <div class="page-actions">
              <button class="primary compact" @click="saveSegments">保存分段</button>
              <button class="ghost" @click="activeTab = 'synth'">下一步：合成</button>
            </div>
          </template>
        </div>

        <div v-if="activeTab === 'synth'" class="tab-content synth-grid">
          <section>
            <h2>合成</h2>
            <p class="hint">使用已保存的项目、角色卡、章节勾选和分段结果。未识别时会先自动识别。</p>
            <div class="strategy-grid">
              <div>
                <label>角色提取</label>
                <select v-model="extractCharacters">
                  <option :value="true">使用 LLM 提取/更新角色</option>
                  <option :value="false">不提取，使用手动角色卡</option>
                </select>
              </div>
              <div>
                <label>对话划分方式</label>
                <select v-model="parseMode">
                  <option value="llm">LLM 划分，失败时正则兜底</option>
                  <option value="regex">正则划分，不调用 LLM 划分</option>
                </select>
              </div>
              <div>
                <label>角色提取范围</label>
                <select v-model="characterScope">
                  <option value="selected">仅勾选章节</option>
                  <option value="all">整书章节</option>
                </select>
              </div>
              <div>
                <label>音色库</label>
                <select v-model="useCloneLibrary">
                  <option :value="true">克隆音色库（50 款精选音色）</option>
                  <option :value="false">预置音色（冰糖 / 茉莉 / 苏打 / 白桦）</option>
                </select>
              </div>
              <div>
                <label>角色提取批次长度（字） <span class="help-tip" title="角色识别时，把多个完整章节合并提交给 LLM 的最大总字数。不会切半章；下一章放不下就开新批，单章超过该值会跳过并提示。">?</span></label>
                <input v-model.number="characterBatchChars" type="number" min="2000" step="1000" />
              </div>
            </div>
            <button class="primary" :disabled="!activeProjectId || busy" @click="regenerateProject">{{ busy ? '生成中...' : '开始合成 / 一键生成' }}</button>
            <div class="downloads" v-if="task?.files?.length">
              <a v-for="file in task.files" :key="file" :href="`/api/download/${file}`">下载 {{ file.split('/').pop() }}</a>
            </div>
          </section>
          <aside>
            <div class="dial" :style="{ '--progress': `${progress}%` }">
              <strong>{{ progress }}%</strong>
              <span>{{ stageLabel }}</span>
            </div>
            <div class="log">
              <p v-for="(item, index) in task?.log || []" :key="index" :class="item.level">{{ item.msg }}</p>
              <p v-if="!task">等待开始。</p>
            </div>
          </aside>
        </div>
      </section>
    </section>

  </main>
</template>
