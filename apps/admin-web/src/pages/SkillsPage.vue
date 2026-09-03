<script setup lang="ts">
import { inject, onMounted, ref } from 'vue'
import AppIcon from '../components/AppIcon.vue'
import { ADMIN_API_KEY } from '../api/client'
import type { AdminApiClient } from '../api/client'

const injectedApi = inject(ADMIN_API_KEY)
if (!injectedApi) throw new Error('Admin API client is not provided')
const api: AdminApiClient = injectedApi

interface SkillItem {
  name: string
  description: string
  path: string
  content: string
}

const skills = ref<SkillItem[]>([])
const selectedSkill = ref<SkillItem | null>(null)
const editingContent = ref('')
const loading = ref(false)
const saving = ref(false)
const noticeMessage = ref('')
const errorMessage = ref('')

async function loadSkills() {
  loading.value = true
  errorMessage.value = ''
  try {
    const list = await api.getSkills()
    skills.value = list || []
    if (skills.value.length > 0 && !selectedSkill.value) {
      selectSkill(skills.value[0])
    }
  } catch (err: any) {
    errorMessage.value = err.message || '读取技能列表失败'
  } finally {
    loading.value = false
  }
}

function selectSkill(skill: SkillItem) {
  selectedSkill.value = skill
  editingContent.value = skill.content
  noticeMessage.value = ''
}

async function saveCurrentSkill() {
  if (!selectedSkill.value) return
  saving.value = true
  noticeMessage.value = ''
  errorMessage.value = ''
  try {
    await api.saveSkill(selectedSkill.value.name, editingContent.value)
    selectedSkill.value.content = editingContent.value
    noticeMessage.value = `技能 ${selectedSkill.value.name} 已保存！Pi Agent 无需重启即可载入新知识。`
    setTimeout(() => { noticeMessage.value = ''; }, 4000)
  } catch (err: any) {
    errorMessage.value = err.message || '保存技能失败'
  } finally {
    saving.value = false
  }
}

onMounted(() => {
  loadSkills()
})
</script>

<template>
  <div class="skills-studio">
    <!-- Top Action Bar -->
    <header class="studio-header">
      <div class="studio-title-group">
        <div class="header-badge">Skills Hub</div>
        <div class="header-text">
          <h1>技能与 SOP 知识库中心</h1>
          <p>热维护 Pi Agent 排查规范、领域规则与排障 SOP，修改后秒级热生效。</p>
        </div>
      </div>
      <div class="studio-actions">
        <span v-if="noticeMessage" class="toast toast--success">{{ noticeMessage }}</span>
        <span v-if="errorMessage" class="toast toast--error">{{ errorMessage }}</span>
        <button class="action-btn btn-save" :disabled="saving || !selectedSkill" @click="saveCurrentSkill">
          <AppIcon name="check" :size="14" />
          <span>{{ saving ? '保存中...' : '保存并热生效' }}</span>
        </button>
      </div>
    </header>

    <!-- Edge-to-Edge Workspace -->
    <div class="studio-workspace">
      <!-- Left: Skills List -->
      <aside class="skills-sidebar">
        <div class="sidebar-meta">
          <span>核心技能卡片 ({{ skills.length }})</span>
        </div>
        <div class="skills-nav">
          <div
            v-for="s in skills"
            :key="s.name"
            class="skill-item"
            :class="{ active: selectedSkill?.name === s.name }"
            @click="selectSkill(s)"
          >
            <div class="item-icon">
              <AppIcon name="terminal" :size="15" />
            </div>
            <div class="item-content">
              <div class="item-title">{{ s.name }}</div>
              <div class="item-desc">{{ s.description }}</div>
            </div>
          </div>
        </div>
      </aside>

      <!-- Right: Full-Height Code Editor -->
      <main class="editor-pane">
        <div v-if="selectedSkill" class="editor-shell">
          <div class="editor-toolbar">
            <div class="editor-file">
              <span class="file-tag">MARKDOWN</span>
              <code class="file-name">{{ selectedSkill.path || `${selectedSkill.name}/SKILL.md` }}</code>
            </div>
            <div class="editor-hints">
              <span>UTF-8</span>
              <span>•</span>
              <span>实时生效</span>
            </div>
          </div>
          <div class="editor-body">
            <textarea
              v-model="editingContent"
              class="code-textarea markdown-editor"
              spellcheck="false"
              placeholder="编写此技能的 Markdown SOP 指引..."
            />
          </div>
        </div>
        <div v-else class="editor-empty">
          <AppIcon name="terminal" :size="32" />
          <p>请选择左侧技能以查看和编辑文档</p>
        </div>
      </main>
    </div>
  </div>
</template>

<style scoped>
.skills-studio {
  display: flex;
  flex-direction: column;
  height: 100%;
  background: #ffffff;
  color: #1d1d1f;
  overflow: hidden;
}

/* Studio Header */
.studio-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 14px 24px;
  border-bottom: 1px solid rgba(0, 0, 0, 0.08);
  background: #fbfbfd;
}
.studio-title-group {
  display: flex;
  align-items: center;
  gap: 12px;
}
.header-badge {
  font-size: 10px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: #059669;
  background: rgba(16, 185, 129, 0.1);
  border: 1px solid rgba(16, 185, 129, 0.25);
  padding: 3px 8px;
  border-radius: 6px;
}
.header-text h1 {
  margin: 0;
  font-size: 16px;
  font-weight: 600;
  color: #1d1d1f;
  letter-spacing: -0.01em;
}
.header-text p {
  margin: 2px 0 0;
  font-size: 12px;
  color: #86868b;
}
.studio-actions {
  display: flex;
  align-items: center;
  gap: 12px;
}
.toast {
  font-size: 12px;
  padding: 5px 12px;
  border-radius: 6px;
  font-weight: 500;
}
.toast--success {
  background: rgba(52, 199, 89, 0.12);
  color: #059669;
  border: 1px solid rgba(52, 199, 89, 0.25);
}
.toast--error {
  background: rgba(255, 59, 48, 0.1);
  color: #dc2626;
  border: 1px solid rgba(255, 59, 48, 0.25);
}
.action-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 34px;
  padding: 0 16px;
  border-radius: 8px;
  border: none;
  background: #000000;
  color: #ffffff;
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.1);
  transition: background 0.15s;
}
.action-btn:hover {
  background: #27272a;
}
.action-btn:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}

/* Workspace */
.studio-workspace {
  display: flex;
  flex: 1;
  min-height: 0;
  overflow: hidden;
}

/* Left Sidebar */
.skills-sidebar {
  width: 310px;
  flex: 0 0 310px;
  display: flex;
  flex-direction: column;
  border-right: 1px solid rgba(0, 0, 0, 0.08);
  background: #fbfbfd;
}
.sidebar-meta {
  padding: 12px 16px;
  border-bottom: 1px solid rgba(0, 0, 0, 0.06);
  font-size: 10px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: #86868b;
}
.skills-nav {
  flex: 1;
  overflow-y: auto;
  padding: 10px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.skill-item {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  padding: 12px;
  border-radius: 8px;
  border: 1px solid rgba(0, 0, 0, 0.06);
  background: #ffffff;
  cursor: pointer;
  transition: all 0.12s;
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.02);
}
.skill-item:hover {
  background: #f5f5f7;
  border-color: rgba(0, 0, 0, 0.12);
}
.skill-item.active {
  background: #ffffff;
  border-color: #0071e3;
  box-shadow: 0 0 0 1px #0071e3, 0 2px 8px rgba(0, 113, 227, 0.1);
}
.item-icon {
  width: 28px;
  height: 28px;
  border-radius: 6px;
  background: #f5f5f7;
  color: #6e6e73;
  display: grid;
  place-items: center;
  flex: 0 0 28px;
  margin-top: 1px;
}
.skill-item.active .item-icon {
  background: rgba(0, 113, 227, 0.1);
  color: #0071e3;
}
.item-content {
  flex: 1;
  min-width: 0;
}
.item-title {
  font-size: 13px;
  font-weight: 600;
  color: #1d1d1f;
  font-family: ui-monospace, "SF Mono", monospace;
  margin-bottom: 3px;
}
.skill-item.active .item-title {
  color: #0071e3;
}
.item-desc {
  font-size: 11px;
  color: #6e6e73;
  line-height: 1.4;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

/* Editor Pane */
.editor-pane {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-width: 0;
  background: #ffffff;
}
.editor-shell {
  display: flex;
  flex-direction: column;
  height: 100%;
}
.editor-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 20px;
  background: #f8f9fa;
  border-bottom: 1px solid rgba(0, 0, 0, 0.08);
}
.editor-file {
  display: flex;
  align-items: center;
  gap: 8px;
}
.file-tag {
  font-size: 9px;
  font-weight: 700;
  letter-spacing: 0.08em;
  padding: 2px 6px;
  border-radius: 4px;
  background: rgba(0, 113, 227, 0.1);
  color: #0071e3;
}
.file-name {
  font-size: 12px;
  color: #1d1d1f;
  font-family: "SF Mono", Menlo, Consolas, monospace;
}
.editor-hints {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 11px;
  color: #86868b;
}
.editor-body {
  flex: 1;
  display: flex;
  min-height: 0;
}
.code-textarea {
  flex: 1;
  width: 100%;
  height: 100%;
  border: none;
  outline: none;
  background: #ffffff;
  color: #1d1d1f;
  font-family: "SF Mono", "JetBrains Mono", Menlo, Consolas, monospace;
  font-size: 13px;
  line-height: 1.65;
  padding: 20px 24px;
  resize: none;
  tab-size: 2;
}
.editor-empty {
  display: grid;
  place-content: center;
  height: 100%;
  color: #86868b;
  gap: 12px;
  text-align: center;
}
</style>
