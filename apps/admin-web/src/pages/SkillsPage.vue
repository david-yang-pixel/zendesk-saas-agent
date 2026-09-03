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
  background: #09090b;
  color: #ededed;
  overflow: hidden;
}

/* Studio Header */
.studio-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 16px 24px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.08);
  background: #0c0c0e;
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
  color: #10b981;
  background: rgba(16, 185, 129, 0.12);
  border: 1px solid rgba(16, 185, 129, 0.25);
  padding: 3px 8px;
  border-radius: 6px;
}
.header-text h1 {
  margin: 0;
  font-size: 16px;
  font-weight: 600;
  color: #fff;
  letter-spacing: -0.01em;
}
.header-text p {
  margin: 2px 0 0;
  font-size: 11px;
  color: #71717a;
}
.studio-actions {
  display: flex;
  align-items: center;
  gap: 12px;
}
.toast {
  font-size: 11px;
  padding: 4px 10px;
  border-radius: 6px;
}
.toast--success {
  background: rgba(16, 185, 129, 0.15);
  color: #34d399;
  border: 1px solid rgba(16, 185, 129, 0.3);
}
.toast--error {
  background: rgba(239, 68, 68, 0.15);
  color: #f87171;
  border: 1px solid rgba(239, 68, 68, 0.3);
}
.action-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 32px;
  padding: 0 14px;
  border-radius: 7px;
  border: none;
  background: #ffffff;
  color: #000000;
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
  transition: background 0.15s;
}
.action-btn:hover {
  background: #e4e4e7;
}
.action-btn:disabled {
  opacity: 0.5;
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
  width: 320px;
  flex: 0 0 320px;
  display: flex;
  flex-direction: column;
  border-right: 1px solid rgba(255, 255, 255, 0.08);
  background: #09090b;
}
.sidebar-meta {
  padding: 12px 16px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.06);
  font-size: 10px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: #52525b;
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
  border: 1px solid transparent;
  cursor: pointer;
  transition: all 0.12s;
}
.skill-item:hover {
  background: rgba(255, 255, 255, 0.04);
}
.skill-item.active {
  background: #141416;
  border-color: rgba(255, 255, 255, 0.12);
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.3);
}
.item-icon {
  width: 28px;
  height: 28px;
  border-radius: 6px;
  background: rgba(255, 255, 255, 0.06);
  color: #71717a;
  display: grid;
  place-items: center;
  flex: 0 0 28px;
  margin-top: 2px;
}
.skill-item.active .item-icon {
  background: rgba(16, 185, 129, 0.15);
  color: #34d399;
}
.item-content {
  flex: 1;
  min-width: 0;
}
.item-title {
  font-size: 13px;
  font-weight: 600;
  color: #f4f4f5;
  font-family: ui-monospace, "SF Mono", monospace;
  margin-bottom: 3px;
}
.skill-item.active .item-title {
  color: #ffffff;
}
.item-desc {
  font-size: 11px;
  color: #71717a;
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
  background: #09090b;
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
  background: #0e0e11;
  border-bottom: 1px solid rgba(255, 255, 255, 0.08);
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
  background: rgba(59, 130, 246, 0.15);
  color: #60a5fa;
}
.file-name {
  font-size: 12px;
  color: #d4d4d8;
  font-family: "SF Mono", Menlo, Consolas, monospace;
}
.editor-hints {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 11px;
  color: #52525b;
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
  background: #09090b;
  color: #ededed;
  font-family: "JetBrains Mono", "SF Mono", Menlo, Consolas, monospace;
  font-size: 13px;
  line-height: 1.6;
  padding: 20px 24px;
  resize: none;
  tab-size: 2;
}
.editor-empty {
  display: grid;
  place-content: center;
  height: 100%;
  color: #52525b;
  gap: 12px;
  text-align: center;
}
</style>
