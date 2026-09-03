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
  <div class="skills-hub-page">
    <div class="skills-container">
      <header class="skills-header">
        <div class="header-left">
          <div class="badge-tag">Skills Hub</div>
          <h2>技能与 SOP 知识库中心</h2>
          <p>维护与热更新 Pi Agent 的领域能力规则、排查手册与 SOP 标准，编辑后即时生效。</p>
        </div>
        <div class="header-actions">
          <button class="btn-save" :disabled="saving || !selectedSkill" @click="saveCurrentSkill">
            <AppIcon name="check" :size="15" />
            <span>{{ saving ? '保存中...' : '保存并热生效' }}</span>
          </button>
        </div>
      </header>

      <div v-if="noticeMessage" class="notice-banner success">{{ noticeMessage }}</div>
      <div v-if="errorMessage" class="notice-banner error">{{ errorMessage }}</div>

      <!-- Main Layout: Cards on top or left, Editor on right -->
      <div class="skills-split">
        <!-- Skills List Sidebar -->
        <div class="skills-list-col">
          <div class="col-title">
            <span>核心技能卡片 ({{ skills.length }})</span>
          </div>
          <div class="skills-cards">
            <div
              v-for="s in skills"
              :key="s.name"
              :class="['skill-card', { active: selectedSkill?.name === s.name }]"
              @click="selectSkill(s)"
            >
              <div class="card-icon">
                <AppIcon name="skills" :size="16" />
              </div>
              <div class="card-info">
                <strong class="skill-title">{{ s.name }}</strong>
                <p class="skill-desc">{{ s.description || '暂无描述' }}</p>
              </div>
            </div>
          </div>
        </div>

        <!-- Markdown Editor -->
        <div class="editor-col" v-if="selectedSkill">
          <div class="editor-header">
            <div class="editor-info">
              <h3>{{ selectedSkill.name }} / SKILL.md</h3>
              <small class="file-path">{{ selectedSkill.path }}</small>
            </div>
          </div>
          <div class="editor-body">
            <textarea
              v-model="editingContent"
              class="markdown-editor"
              placeholder="编写 SKILL.md Markdown 指令与 SOP..."
              spellcheck="false"
            ></textarea>
          </div>
        </div>

        <div class="empty-editor" v-else>
          <p>请选择一个技能以编辑其 SOP 内容</p>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.skills-hub-page {
  min-height: calc(100vh - 68px);
  background: #0d0d0e;
  color: #f5f5f7;
  padding: 32px 48px;
  overflow-y: auto;
  font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", "Segoe UI", sans-serif;
}

.skills-container {
  max-width: 1200px;
  margin: 0 auto;
}

.skills-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-end;
  padding-bottom: 24px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.08);
  margin-bottom: 24px;
}

.badge-tag {
  display: inline-block;
  font-size: 11px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: #10b981;
  background: rgba(16, 185, 129, 0.15);
  padding: 3px 8px;
  border-radius: 6px;
  margin-bottom: 8px;
}

.skills-header h2 {
  margin: 0 0 6px;
  font-size: 24px;
  font-weight: 600;
  letter-spacing: -0.02em;
}

.skills-header p {
  margin: 0;
  color: #a1a1a6;
  font-size: 13px;
  max-width: 650px;
  line-height: 1.5;
}

.btn-save {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  height: 38px;
  padding: 0 18px;
  border-radius: 8px;
  border: none;
  background: #fff;
  color: #000;
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.15s;
}
.btn-save:hover { background: #e4e4e7; }
.btn-save:disabled { opacity: 0.5; cursor: not-allowed; }

.notice-banner {
  padding: 12px 18px;
  border-radius: 8px;
  font-size: 13px;
  margin-bottom: 24px;
}
.notice-banner.success {
  background: rgba(16, 185, 129, 0.15);
  border: 1px solid rgba(16, 185, 129, 0.3);
  color: #34d399;
}
.notice-banner.error {
  background: rgba(239, 68, 68, 0.15);
  border: 1px solid rgba(239, 68, 68, 0.3);
  color: #f87171;
}

/* Split Layout */
.skills-split {
  display: grid;
  grid-template-columns: 360px 1fr;
  gap: 24px;
  min-height: 600px;
}

.skills-list-col {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.col-title {
  font-size: 12px;
  font-weight: 600;
  color: #71717a;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  padding-bottom: 4px;
}

.skills-cards {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.skill-card {
  display: flex;
  gap: 14px;
  background: #141416;
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 12px;
  padding: 16px;
  cursor: pointer;
  transition: all 0.15s;
}
.skill-card:hover {
  background: #18181b;
  border-color: rgba(255, 255, 255, 0.18);
}
.skill-card.active {
  background: #1c1c1f;
  border-color: #10b981;
  box-shadow: 0 0 0 1px #10b981;
}

.card-icon {
  width: 32px;
  height: 32px;
  border-radius: 8px;
  background: rgba(16, 185, 129, 0.15);
  color: #34d399;
  display: grid;
  place-items: center;
  flex: 0 0 32px;
}

.card-info {
  flex: 1;
  min-width: 0;
}
.skill-title {
  display: block;
  font-size: 14px;
  font-weight: 600;
  color: #fff;
  margin-bottom: 4px;
  font-family: ui-monospace, monospace;
}
.skill-desc {
  margin: 0;
  font-size: 11px;
  color: #a1a1a6;
  line-height: 1.4;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

/* Editor Col */
.editor-col {
  background: #141416;
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 12px;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.editor-header {
  padding: 16px 20px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.08);
  background: #111113;
}
.editor-info h3 {
  margin: 0 0 4px;
  font-size: 16px;
  font-family: "JetBrains Mono", monospace;
  color: #fff;
}
.file-path {
  font-size: 11px;
  color: #71717a;
  font-family: ui-monospace, monospace;
}

.editor-body {
  flex: 1;
  display: flex;
}

.markdown-editor {
  flex: 1;
  width: 100%;
  min-height: 520px;
  background: #0d0d0e;
  border: none;
  color: #f4f4f5;
  font-family: "JetBrains Mono", SFMono-Regular, Menlo, monospace;
  font-size: 13px;
  line-height: 1.6;
  padding: 20px;
  resize: vertical;
  outline: none;
}
.markdown-editor:focus {
  background: #09090b;
}

.empty-editor {
  display: grid;
  place-items: center;
  background: #141416;
  border: 1px dashed rgba(255, 255, 255, 0.1);
  border-radius: 12px;
  color: #71717a;
}
</style>
