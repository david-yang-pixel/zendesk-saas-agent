import type {
  AdminConfig,
  AdminConfigSnapshot,
  ConnectionProbe,
  ModelCatalog,
  ModelCredentialReplaceResult,
  ModelCredentialStatus,
  OverviewData,
  RegistryData,
  RunListData,
  SandboxPolicy,
  ControlMode,
  SafetySettings,
  MeshHealthResponse,
} from '../types'
import type { FailureItem, MetricCard, QueueHealth, RecentRun, RunRecord, SkillRecord, ToolRecord } from '../types'
import type { InjectionKey } from 'vue'
import { adminApiBasePath, adminBasePath } from '../base-path'

export { adminApiBasePath, adminBasePath } from '../base-path'

export interface AdminApiTransport {
  request<T>(path: string, init?: RequestInit): Promise<T>
  setCsrfToken?(token: string | null): void
}

export class AdminApiHttpError extends Error {
  constructor(readonly status: number, readonly path: string, message: string) {
    super(message)
    this.name = 'AdminApiHttpError'
  }
}

class FetchAdminApiTransport implements AdminApiTransport {
  private csrfToken: string | null = null

  constructor(private readonly baseUrl = adminBasePath()) {}

  setCsrfToken(token: string | null) {
    this.csrfToken = token
  }

  async request<T>(path: string, init?: RequestInit): Promise<T> {
    const response = await fetch(`${this.baseUrl}${path}`, {
      ...init,
      credentials: 'same-origin',
      headers: {
        Accept: 'application/json',
        ...(init?.method && !['GET', 'HEAD', 'OPTIONS'].includes(init.method.toUpperCase()) && this.csrfToken
          ? { 'X-CSRF-Token': this.csrfToken }
          : {}),
        ...init?.headers,
      },
    })
    if (!response.ok) {
      let message = response.statusText || 'Admin API request failed'
      try {
        const body = (await response.json()) as { detail?: string }
        if (body.detail) message = body.detail
      } catch {
        // Keep the HTTP status message when the error body is not JSON.
      }
      throw new AdminApiHttpError(response.status, path, message)
    }
    return (await response.json()) as T
  }
}

interface ApiModelConfig {
  provider: string
  base_url: string
  model: string
  credential_ref: string
}

interface ApiSubagentSlotConfig extends ApiModelConfig {
  slot_id: string
  enabled: boolean
  max_parallel: number
  context_window_tokens: number
  max_tool_rounds: number
}

interface ApiProcessingRules {
  saas_tag: string
  saas_assignee: string
  statuses: string[]
  human_aliases: string[]
  reply_enabled: boolean
  default_locale: string
  footers: Record<string, string>
}

interface ApiAdminConfig {
  desired_state: 'running' | 'paused'
  root_model: ApiModelConfig
  subagent_slots: ApiSubagentSlotConfig[]
  rules: ApiProcessingRules
  enabled_skills: string[]
  enabled_tools: string[]
  context_window_tokens: number
  soft_compaction_percent: number
  hard_checkpoint_percent: number
  max_tool_rounds: number
}

interface ApiConfigSnapshot {
  revision: string
  config: ApiAdminConfig
  created_at: string
  actor: string
}

interface ApiMetric {
  label: string
  value: string
  detail: string
  trend: string | null
  trend_tone: MetricCard['trendTone'] | null
  icon: MetricCard['icon']
  accent: MetricCard['accent']
}

interface ApiQueueHealth {
  label: string
  count: number
  description: string
  tone: QueueHealth['tone']
}

interface ApiFailureItem {
  id: string
  title: string
  detail: string
  time: string
  tone: FailureItem['tone']
}

interface ApiRecentRun {
  ticket_id: number
  subject: string
  status: RecentRun['status']
  status_label: string
  started_at: string
  duration: string
  steps: number | null
}

interface ApiRunRecord extends ApiRecentRun {
  session_id: string
  event_id: string | null
  expected_event_id: string | null
  attempts: number
  config_revision: string | null
  question: string | null
  todo_completed: number | null
  todo_total: number | null
  tool_calls: number | null
  subagents: number | null
  evidence: number | null
  checkpoint: string | null
  sandbox: RunRecord['sandbox']
  outcome: string
  failure_reason: string | null
  retry_classification: string | null
  snapshot_cursor: string | null
}

interface ApiOverview {
  desired_state: 'running' | 'paused'
  config_revision: string
  heartbeat: string | null
  metrics: ApiMetric[]
  queue_health: ApiQueueHealth[]
  failures: ApiFailureItem[]
  recent_runs: ApiRecentRun[]
}

interface ApiRunList {
  items: ApiRunRecord[]
  total: number
}

interface ApiSkillRecord {
  id: string
  name: string
  description: string
  version: string
  assigned_todos: string
  status: SkillRecord['status']
  updated_at: string
}

interface ApiToolRecord {
  id: string
  name: string
  description: string
  scope: ToolRecord['scope']
  contract: string
  connection: ToolRecord['connection']
  latency: string
}

interface ApiRegistry {
  skills: ApiSkillRecord[]
  tools: ApiToolRecord[]
}

interface ApiSandboxPolicy {
  attempt_1: { cpu: number; memory_mib: number; timeout_seconds: number }
  attempt_2: { cpu: number; memory_mib: number; timeout_seconds: number }
  retry_condition: string
}

interface ApiConnectionProbe {
  tool_id: string
  status: 'connected' | 'degraded' | 'untested'
  latency_ms: number | null
  error_code: ConnectionProbe['errorCode']
  detail: string | null
}

interface ApiModelCredentialBinding {
  provider: string
  base_url: string
  model: string
  credential_ref: string
}

interface ApiModelCredentialStatus {
  credential_ref: string
  configured: boolean
  masked_value: string | null
  updated_at: string | null
  bindings: ApiModelCredentialBinding[]
}

interface ApiModelCredentialList {
  items: ApiModelCredentialStatus[]
}

interface ApiModelCredentialReplaceResult {
  credential: ApiModelCredentialStatus
  probe: { status: 'connected' | 'degraded'; latency_ms: number | null; error_code: string | null }
}

export interface AdminSession {
  username: string
  csrfToken: string
  forcePasswordChange: boolean
}

interface ApiAdminSession {
  username: string
  csrf_token: string
  force_password_change: boolean
}

function toAdminSession(value: ApiAdminSession): AdminSession {
  return {
    username: value.username,
    csrfToken: value.csrf_token,
    forcePasswordChange: value.force_password_change,
  }
}

function toModelCredential(value: ApiModelCredentialStatus): ModelCredentialStatus {
  return {
    credentialRef: value.credential_ref,
    configured: value.configured,
    maskedValue: value.masked_value,
    updatedAt: value.updated_at,
    bindings: value.bindings.map((binding) => ({
      provider: binding.provider,
      baseUrl: binding.base_url,
      model: binding.model,
      credentialRef: binding.credential_ref,
    })),
  }
}

function toRecentRun(value: ApiRecentRun): RecentRun {
  return {
    ticketId: value.ticket_id,
    subject: value.subject,
    status: value.status,
    statusLabel: value.status_label,
    startedAt: value.started_at,
    duration: value.duration,
    steps: value.steps,
  }
}

function toRun(value: ApiRunRecord): RunRecord {
  return {
    ...toRecentRun(value),
    sessionId: value.session_id,
    eventId: value.event_id,
    expectedEventId: value.expected_event_id,
    attempts: value.attempts,
    configRevision: value.config_revision,
    question: value.question,
    todoCompleted: value.todo_completed,
    todoTotal: value.todo_total,
    toolCalls: value.tool_calls,
    subagents: value.subagents,
    evidence: value.evidence,
    checkpoint: value.checkpoint,
    sandbox: value.sandbox,
    outcome: value.outcome,
    failureReason: value.failure_reason,
    retryClassification: value.retry_classification,
    snapshotCursor: value.snapshot_cursor,
  }
}

function toSkill(value: ApiSkillRecord): SkillRecord {
  return {
    id: value.id,
    name: value.name,
    description: value.description,
    version: value.version,
    assignedTodos: value.assigned_todos,
    status: value.status,
    updatedAt: value.updated_at,
  }
}

function toTool(value: ApiToolRecord): ToolRecord {
  return { ...value }
}

function toConfig(value: ApiAdminConfig): AdminConfig {
  return {
    desiredState: value.desired_state,
    rootModel: {
      provider: value.root_model.provider,
      baseUrl: value.root_model.base_url,
      model: value.root_model.model,
      credentialRef: value.root_model.credential_ref,
    },
    subagentSlots: value.subagent_slots.map((slot) => ({
      slotId: slot.slot_id,
      enabled: slot.enabled,
      provider: slot.provider,
      baseUrl: slot.base_url,
      model: slot.model,
      credentialRef: slot.credential_ref,
      maxParallel: slot.max_parallel,
      contextWindowTokens: slot.context_window_tokens,
      maxToolRounds: slot.max_tool_rounds,
    })),
    rules: {
      saasTag: value.rules.saas_tag,
      assignee: value.rules.saas_assignee,
      statuses: [...value.rules.statuses],
      humanAliases: [...value.rules.human_aliases],
      replyEnabled: value.rules.reply_enabled,
      footerLocale: value.rules.default_locale,
      footers: { ...value.rules.footers },
    },
    enabledSkills: [...value.enabled_skills],
    enabledTools: [...value.enabled_tools],
    contextWindowTokens: value.context_window_tokens,
    softCompactionPercent: value.soft_compaction_percent,
    hardCheckpointPercent: value.hard_checkpoint_percent,
    maxToolRounds: value.max_tool_rounds,
  }
}

function toApiConfig(value: AdminConfig): ApiAdminConfig {
  return {
    desired_state: value.desiredState,
    root_model: {
      provider: value.rootModel.provider,
      base_url: value.rootModel.baseUrl,
      model: value.rootModel.model,
      credential_ref: value.rootModel.credentialRef,
    },
    subagent_slots: value.subagentSlots.map((slot) => ({
      slot_id: slot.slotId,
      enabled: slot.enabled,
      provider: slot.provider,
      base_url: slot.baseUrl,
      model: slot.model,
      credential_ref: slot.credentialRef,
      max_parallel: slot.maxParallel,
      context_window_tokens: slot.contextWindowTokens,
      max_tool_rounds: slot.maxToolRounds,
    })),
    rules: {
      saas_tag: value.rules.saasTag,
      saas_assignee: value.rules.assignee,
      statuses: [...value.rules.statuses],
      human_aliases: [...value.rules.humanAliases],
      reply_enabled: value.rules.replyEnabled,
      default_locale: value.rules.footerLocale,
      footers: { ...value.rules.footers },
    },
    enabled_skills: [...value.enabledSkills],
    enabled_tools: [...value.enabledTools],
    context_window_tokens: value.contextWindowTokens,
    soft_compaction_percent: value.softCompactionPercent,
    hard_checkpoint_percent: value.hardCheckpointPercent,
    max_tool_rounds: value.maxToolRounds,
  }
}

function toSnapshot(value: ApiConfigSnapshot): AdminConfigSnapshot {
  return {
    revision: value.revision,
    config: toConfig(value.config),
    createdAt: value.created_at,
    actor: value.actor,
  }
}

export interface AdminApiClient {
  getSession(): Promise<AdminSession>
  login(input: { username: string; password: string }): Promise<AdminSession>
  logout(): Promise<void>
  changePassword(input: { currentPassword: string; newPassword: string }): Promise<void>
  getConfig(): Promise<AdminConfigSnapshot>
  saveConfig(input: { expectedRevision: string; config: AdminConfig }): Promise<AdminConfigSnapshot>
  getSandboxPolicy(): Promise<SandboxPolicy>
  getOverview(): Promise<OverviewData>
  getRuns(input?: { query?: string; status?: string }): Promise<RunListData>
  getRun(sessionId: string): Promise<RunRecord>
  getRegistry(): Promise<RegistryData>
  testTool(toolId: string): Promise<ConnectionProbe>
  getModelCredentials(): Promise<ModelCredentialStatus[]>
  replaceModelCredential(input: { credentialRef: string; secret: string }): Promise<ModelCredentialReplaceResult>
  discoverModels(input: { provider: string; baseUrl: string; secret: string }): Promise<ModelCatalog>
  getTraces(input?: { query?: string; status?: string }): Promise<{ items: any[]; total: number }>
  getTrace(ticketId: number, jobId?: string): Promise<any>
  retryJob(jobId: string): Promise<{ ok: boolean; message: string }>
  skipJob(jobId: string): Promise<{ ok: boolean; job_id: string; message: string }>
  escalateJob(jobId: string): Promise<{ ok: boolean; ticket_id: number; message: string }>
  getControlMode(): Promise<{ mode: ControlMode }>
  setControlMode(mode: ControlMode): Promise<{ ok: boolean; mode: ControlMode }>
  getSafetySettings(): Promise<SafetySettings>
  saveSafetySettings(data: SafetySettings): Promise<{ ok: boolean; message: string }>
  testFeishuWebhook(data?: { webhook_url?: string }): Promise<{ ok: boolean; status_code: number; latency_ms: number; message: string }>
  getMeshHealth(): Promise<MeshHealthResponse>
  getModelSettings(): Promise<any>
  saveModelSettings(data: any): Promise<{ ok: boolean; message: string }>
  testModelConnection(data: any): Promise<{ ok: boolean; status_code: number; latency_ms: number; message: string; sample_text: string }>
  detectReasoningLevels(data: { base_url: string; model: string; api_key?: string }): Promise<{ ok: boolean; levels: string[]; message: string }>
  getMcpMesh(): Promise<{ services: any[]; total_tools: number }>
  getSkills(): Promise<any[]>
  saveSkill(skillName: string, content: string): Promise<{ ok: boolean; message: string }>
  getLiveLogs(tail?: number): Promise<{ lines: string[]; total: number }>
}

export function createAdminApiClient(transport: AdminApiTransport = new FetchAdminApiTransport()): AdminApiClient {
  return {
    async getSession() {
      const session = toAdminSession(await transport.request<ApiAdminSession>('/api/auth/session'))
      transport.setCsrfToken?.(session.csrfToken)
      return session
    },
    async login({ username, password }) {
      const session = toAdminSession(await transport.request<ApiAdminSession>('/api/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, password }),
      }))
      transport.setCsrfToken?.(session.csrfToken)
      return session
    },
    async logout() {
      await transport.request('/api/auth/logout', { method: 'POST' })
      transport.setCsrfToken?.(null)
    },
    async changePassword({ currentPassword, newPassword }) {
      await transport.request('/api/auth/change-password', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }),
      })
      transport.setCsrfToken?.(null)
    },
    async getConfig() {
      return toSnapshot(await transport.request<ApiConfigSnapshot>('/api/config'))
    },
    async saveConfig({ expectedRevision, config }) {
      return toSnapshot(
        await transport.request<ApiConfigSnapshot>('/api/config', {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ expected_revision: expectedRevision, config: toApiConfig(config) }),
        }),
      )
    },
    async getSandboxPolicy() {
      const value = await transport.request<ApiSandboxPolicy>('/api/config/sandbox-policy')
      return {
        attempt1: { cpu: value.attempt_1.cpu, memoryMib: value.attempt_1.memory_mib, timeoutSeconds: value.attempt_1.timeout_seconds },
        attempt2: { cpu: value.attempt_2.cpu, memoryMib: value.attempt_2.memory_mib, timeoutSeconds: value.attempt_2.timeout_seconds },
        retryCondition: value.retry_condition,
      }
    },
    async getOverview() {
      const value = await transport.request<ApiOverview>('/api/overview')
      return {
        desiredState: value.desired_state,
        configRevision: value.config_revision,
        heartbeat: value.heartbeat,
        metrics: value.metrics.map((item) => ({
          label: item.label,
          value: item.value,
          detail: item.detail,
          trend: item.trend ?? undefined,
          trendTone: item.trend_tone ?? undefined,
          icon: item.icon,
          accent: item.accent,
        })),
        queueHealth: value.queue_health.map((item) => ({ ...item })),
        failures: value.failures.map((item) => ({ ...item })),
        recentRuns: value.recent_runs.map(toRecentRun),
      }
    },
    async getRuns(input = {}) {
      const params = new URLSearchParams()
      if (input.query?.trim()) params.set('query', input.query.trim())
      if (input.status && input.status !== 'all') params.set('status', input.status)
      const suffix = params.toString() ? `?${params.toString()}` : ''
      const value = await transport.request<ApiRunList>(`/api/runs${suffix}`)
      return { items: value.items.map(toRun), total: value.total }
    },
    async getRun(sessionId) {
      return toRun(await transport.request<ApiRunRecord>(`/api/runs/${encodeURIComponent(sessionId)}`))
    },
    async getRegistry() {
      const value = await transport.request<ApiRegistry>('/api/registry')
      return { skills: value.skills.map(toSkill), tools: value.tools.map(toTool) }
    },
    async testTool(toolId) {
      const value = await transport.request<ApiConnectionProbe>(`/api/registry/tools/${encodeURIComponent(toolId)}/test`, { method: 'POST' })
      return {
        toolId: value.tool_id,
        status: value.status,
        latencyMs: value.latency_ms,
        errorCode: value.error_code,
        detail: value.detail,
      }
    },
    async getModelCredentials() {
      const value = await transport.request<ApiModelCredentialList>('/api/model-credentials')
      return value.items.map(toModelCredential)
    },
    async replaceModelCredential({ credentialRef, secret }) {
      const value = await transport.request<ApiModelCredentialReplaceResult>('/api/model-credentials', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ credential_ref: credentialRef, secret }),
      })
      return {
        credential: toModelCredential(value.credential),
        probe: {
          status: value.probe.status,
          latencyMs: value.probe.latency_ms,
          errorCode: value.probe.error_code,
        },
      }
    },
    async discoverModels({ provider, baseUrl, secret }) {
      const value = await transport.request<{ provider: string; base_url: string; models: string[]; latency_ms: number }>('/api/model-catalog', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ provider, base_url: baseUrl, secret }),
      })
      return {
        provider: value.provider,
        baseUrl: value.base_url,
        models: value.models,
        latencyMs: value.latency_ms,
      }
    },
    async getTraces(input = {}) {
      const params = new URLSearchParams()
      if (input.query?.trim()) params.set('query', input.query.trim())
      if (input.status && input.status !== 'all') params.set('status', input.status)
      const suffix = params.toString() ? `?${params.toString()}` : ''
      return transport.request(`/api/traces${suffix}`)
    },
    async getTrace(ticketId, jobId) {
      const query = jobId ? `?job_id=${encodeURIComponent(jobId)}` : ''
      return transport.request(`/api/traces/${ticketId}${query}`)
    },
    async retryJob(jobId) {
      return transport.request(`/api/traces/${jobId}/retry`, { method: 'POST' })
    },
    async skipJob(jobId) {
      return transport.request(`/api/traces/${jobId}/skip`, { method: 'POST' })
    },
    async escalateJob(jobId) {
      return transport.request(`/api/traces/${jobId}/escalate`, { method: 'POST' })
    },
    async getControlMode() {
      return transport.request('/api/control/mode')
    },
    async setControlMode(mode) {
      return transport.request('/api/control/mode', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mode }),
      })
    },
    async getSafetySettings() {
      return transport.request('/api/settings/safety')
    },
    async saveSafetySettings(data) {
      return transport.request('/api/settings/safety', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data),
      })
    },
    async testFeishuWebhook(data = {}) {
      return transport.request('/api/settings/feishu/test', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data),
      })
    },
    async getMeshHealth() {
      return transport.request('/api/system/mesh-health')
    },
    async getModelSettings() {
      return transport.request('/api/settings/model')
    },
    async saveModelSettings(data) {
      return transport.request('/api/settings/model', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data),
      })
    },
    async testModelConnection(data) {
      return transport.request('/api/settings/model/test', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data),
      })
    },
    async detectReasoningLevels(data) {
      return transport.request('/api/settings/model/reasoning-levels', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data),
      })
    },
    async getMcpMesh() {
      return transport.request('/api/mcp/mesh')
    },
    async getSkills() {
      return transport.request('/api/skills')
    },
    async saveSkill(skillName, content) {
      return transport.request(`/api/skills/${encodeURIComponent(skillName)}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ content }),
      })
    },
    async getLiveLogs(tail = 150) {
      return transport.request(`/api/logs/live?tail=${tail}`)
    },
  }
}

export const ADMIN_API_KEY: InjectionKey<AdminApiClient> = Symbol('casework-admin-api')
