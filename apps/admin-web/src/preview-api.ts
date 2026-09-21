import type { AdminApiClient } from './api/client'
import type { AdminConfigSnapshot, RunRecord } from './types'

let snapshot: AdminConfigSnapshot = {
  revision: 'cfg-2026.09.02',
  createdAt: '2026-09-02T05:05:00Z',
  actor: 'production-cutover',
  config: {
    desiredState: 'running',
    rootModel: {
      provider: 'openai',
      baseUrl: 'https://api.example.com/v1',
      model: 'gpt-5.6-terra',
      credentialRef: 'openai-primary',
    },
    subagentSlots: [{
      slotId: 'subagent-1',
      enabled: false,
      provider: 'openai',
      baseUrl: 'https://api.example.com/v1',
      model: 'gpt-5.6-luna',
      credentialRef: 'subagent-primary',
      contextWindowTokens: 32_000,
      maxToolRounds: 12,
      maxParallel: 1,
    }],
    rules: {
      saasTag: 'saas',
      assignee: '40685275477140',
      statuses: ['new', 'open'],
      humanAliases: ['human', '人工', '真人'],
      replyEnabled: true,
      footerLocale: 'zh',
      footers: { zh: '此回复由 Dify AI Agent 发送。' },
    },
    enabledSkills: ['core-casework', 'app-run-investigation'],
    enabledTools: ['get_ticket', 'db_gateway_query', 'signoz_search_logs'],
    contextWindowTokens: 272_000,
    softCompactionPercent: 60,
    hardCheckpointPercent: 80,
    maxToolRounds: 24,
  },
}

const previewRuns: RunRecord[] = [
  {
    ticketId: 3726,
    subject: 'QQ 群处理应用没有运行日志',
    status: 'completed',
    statusLabel: '已完成',
    startedAt: '13:05:00',
    duration: '19s',
    steps: 2,
    sessionId: 'session-preview-3726',
    eventId: 'evt-preview-3726',
    expectedEventId: 'messaging:evt-preview-3726',
    attempts: 1,
    configRevision: snapshot.revision,
    question: '为什么应用没有运行日志？',
    todoCompleted: 2,
    todoTotal: 2,
    toolCalls: 2,
    subagents: 0,
    evidence: 2,
    checkpoint: 'none',
    sandbox: 'destroyed',
    outcome: 'answer',
    failureReason: null,
    retryClassification: null,
    snapshotCursor: 'messaging:evt-preview-3726',
  },
  {
    ticketId: 4217,
    subject: '日语测试邮件自动回复',
    status: 'awaiting_customer',
    statusLabel: '等待客户',
    startedAt: '12:42:18',
    duration: '31s',
    steps: 4,
    sessionId: 'session-preview-4217',
    eventId: 'evt-preview-4217',
    expectedEventId: 'messaging:evt-preview-4217',
    attempts: 1,
    configRevision: snapshot.revision,
    question: '可以使用日语回复吗？',
    todoCompleted: 3,
    todoTotal: 4,
    toolCalls: 4,
    subagents: 0,
    evidence: 3,
    checkpoint: 'soft',
    sandbox: 'destroyed',
    outcome: 'ask',
    failureReason: null,
    retryClassification: null,
    snapshotCursor: 'messaging:evt-preview-4217',
  },
]

export const previewAdminApi: AdminApiClient = {
  async getSession() { return { username: 'admin', csrfToken: 'preview', forcePasswordChange: false } },
  async login() { return this.getSession() },
  async logout() { return undefined },
  async changePassword() { return undefined },
  async getConfig() { return snapshot },
  async saveConfig(input) {
    snapshot = {
      ...snapshot,
      revision: 'cfg-preview-saved',
      createdAt: new Date().toISOString(),
      actor: 'preview-admin',
      config: input.config,
    }
    return snapshot
  },
  async getSandboxPolicy() {
    return {
      attempt1: { cpu: 0.5, memoryMib: 1024, timeoutSeconds: 1800 },
      attempt2: { cpu: 1, memoryMib: 1024, timeoutSeconds: 1800 },
      retryCondition: 'resource_exhausted_only',
    }
  },
  async getOverview() {
    return {
      desiredState: snapshot.config.desiredState,
      configRevision: snapshot.revision,
      heartbeat: '13:18:42',
      metrics: [
        { label: '今日处理工单', value: '18', detail: '过去 24 小时', trend: '+4', trendTone: 'success', icon: 'activity', accent: 'blue' },
        { label: '当前队列', value: '0', detail: '无等待任务', icon: 'clock', accent: 'green' },
        { label: '成功完成率', value: '94%', detail: '17 / 18 已完成', icon: 'check', accent: 'violet' },
        { label: '需关注', value: '1', detail: '最近一次模型失败', icon: 'alert', accent: 'amber' },
      ],
      queueHealth: [
        { label: 'Active', count: 0, description: 'Queued, leased, or running jobs', tone: 'info' },
        { label: 'Completed', count: 17, description: 'Completed durable jobs', tone: 'success' },
        { label: 'Other terminal', count: 1, description: 'Failed or cancelled jobs', tone: 'danger' },
      ],
      failures: [{ id: 'preview-failure', title: 'Ticket #4199', detail: 'Model tool repair exhausted', time: '12:18', tone: 'danger' }],
      recentRuns: previewRuns,
    }
  },
  async getRuns() { return { items: previewRuns, total: previewRuns.length } },
  async getRun(sessionId) { return previewRuns.find((run) => run.sessionId === sessionId) ?? previewRuns[0]! },
  async getRegistry() {
    return {
      skills: [
        { id: 'core-casework', name: 'Core casework', description: '工单调查与证据约束', version: 'v1', assignedTodos: 'all', status: 'core', updatedAt: '刚刚' },
        { id: 'app-run-investigation', name: 'App run investigation', description: '应用运行与日志调查', version: 'v1', assignedTodos: 'runtime', status: 'enabled', updatedAt: '刚刚' },
      ],
      tools: [
        { id: 'get_ticket', name: 'Zendesk ticket', description: '读取最新工单快照', scope: 'root + subagents', contract: 'typed', connection: 'connected', latency: '42ms' },
        { id: 'db_gateway_query', name: 'DB gateway', description: '只读生产数据库查询', scope: 'root + subagents', contract: 'typed', connection: 'connected', latency: '86ms' },
      ],
    }
  },
  async testTool(toolId) { return { toolId, status: 'connected', latencyMs: 42, errorCode: null, detail: null } },
  async getModelCredentials() {
    return [{
      credentialRef: 'openai-primary',
      configured: true,
      maskedValue: '••••••••2f91',
      updatedAt: '2026-09-02T05:05:00Z',
      bindings: [snapshot.config.rootModel],
    }]
  },
  async replaceModelCredential({ credentialRef }) {
    return {
      credential: { credentialRef, configured: true, maskedValue: '••••••••8a21', updatedAt: new Date().toISOString(), bindings: [snapshot.config.rootModel] },
      probe: { status: 'connected', latencyMs: 38, errorCode: null },
    }
  },
  async discoverModels({ provider, baseUrl }) {
    return { provider, baseUrl, models: ['gpt-5.6-terra', 'gpt-5.6-luna'], latencyMs: 41 }
  },
  async getTraces() {
    return {
      items: [
        {
          ticket_id: 3726,
          job_id: 'job-preview-1',
          session_id: 'sess-preview-1',
          status: 'completed',
          created_at: '2026-09-02T10:00:00Z',
          finished_at: '2026-09-02T10:00:25Z',
          duration: '25s',
          customer_message: 'Why is my workflow returning 500 internal server error?',
          requester_email: 'customer@acme.com',
          model: 'gpt-5.6-terra',
          tool_calls_count: 2,
          reply_preview: 'Hello! After investigating your app workflow logs in SigNoz...',
          delivery_status: 'sent',
          has_trace: true,
        },
      ],
      total: 1,
    }
  },
  async getTrace(ticketId) {
    return {
      ticket_id: ticketId,
      job_id: 'job-preview-1',
      status: 'completed',
      created_at: '2026-09-02T10:00:00Z',
      finished_at: '2026-09-02T10:00:25Z',
      duration: '25s',
      customer_message: 'Why is my workflow returning 500 internal server error?',
      requester_email: 'customer@acme.com',
      model: 'gpt-5.6-terra',
      steps: [
        {
          type: 'thinking',
          thinking: 'The customer is reporting a 500 error on workflow execution. I will query SigNoz for logs.',
        },
        {
          type: 'tool_call',
          toolName: 'signoz_search_logs',
          service: 'signoz',
          arguments: { query: 'service_name = "dify-api" AND status >= 500' },
          result: [{ timestamp: '2026-09-02T09:59:58Z', message: 'Connection timeout to external tool' }],
        },
      ],
      final_reply: 'Hello!\n\nWe checked the server telemetry for your workflow run. The 500 error was caused by an upstream timeout from the third-party HTTP node.\n\n---\nThis reply was sent by Dify AI Agent. If this does not resolve your issue, reply “human”.',
      delivery: {
        channel: 'browser-relay',
        status: 'sent',
        total_duration_ms: 1250,
      },
    }
  },
  async retryJob(jobId) {
    return { ok: true, message: `Job ${jobId} requeued` }
  },
  async getModelSettings() {
    return {
      provider: 'openai',
      base_url: 'https://api.tokener.dev/v1',
      model: 'gpt-5.6-terra',
      api_key_masked: 'sk-hIey...GlqfZ',
      has_key: true,
      reasoning: true,
      timeout_seconds: 120,
    }
  },
  async saveModelSettings(data) {
    return { ok: true, message: 'Settings saved' }
  },
  async testModelConnection(data) {
    return {
      ok: true,
      status_code: 200,
      latency_ms: 142,
      message: 'Connection successful',
      sample_text: 'pong',
    }
  },
  async getMcpMesh() {
    return {
      services: [
        {
          id: 'openconnector',
          name: 'openconnector',
          label: 'OpenConnector Read-only Investigation Gateway',
          service_type: 'query',
          endpoint: 'https://open-connector.yyg066340.workers.dev/mcp',
          online: true,
          latency_ms: 45,
          tool_count: 4,
          tools: [
            { name: 'list_connections', description: 'List scoped connections' },
            { name: 'search_actions', description: 'Search scoped actions' },
            { name: 'get_action_guide', description: 'Inspect live action schema and risk' },
            { name: 'execute_action', description: 'Execute verified read-only actions' },
          ],
        },
        {
          id: 'zendesk-browser-relay',
          name: 'zendesk-browser-relay',
          label: 'Zendesk Browser Relay (Delivery)',
          service_type: 'action',
          endpoint: 'https://zendesk.smlershou.top/zendesk-browser-relay',
          online: true,
          latency_ms: 68,
          tool_count: 1,
          tools: [{ name: 'send_zendesk_native_message', description: 'Send browser native messaging reply' }],
        },
      ],
      total_tools: 5,
    }
  },
  async getSkills() {
    return [
      {
        name: 'core-casework',
        description: 'Core casework standards',
        path: '/app/skills/core-casework/SKILL.md',
        content: '# Core Casework\n\nStandards...',
      },
    ]
  },
  async saveSkill(skillName, content) {
    return { ok: true, message: `Saved skill ${skillName}` }
  },
  async skipJob(jobId) {
    return { ok: true, job_id: jobId, message: `Job ${jobId} skipped` }
  },
  async escalateJob(jobId) {
    return { ok: true, ticket_id: 1234, message: `Job ${jobId} escalated` }
  },
  async getControlMode() {
    return { mode: 'live' }
  },
  async setControlMode(mode) {
    return { ok: true, mode }
  },
  async getSafetySettings() {
    return {
      escalation_keywords: ['退款', '投诉', '人工'],
      auto_escalate_on_fail: true,
      max_retries: 2,
      ticket_blacklist: [4308],
      requester_blacklist: [],
      feishu_webhook_url: '',
    }
  },
  async saveSafetySettings(_data) {
    return { ok: true, message: 'Saved safety settings' }
  },
  async testFeishuWebhook(_data) {
    return { ok: true, status_code: 200, latency_ms: 120, message: 'Test message sent' }
  },
  async getMeshHealth() {
    return {
      items: [
        { name: 'db', label: 'PostgreSQL', status: 'healthy', latency_ms: 5 },
        { name: 'llm', label: 'LLM Gateway', status: 'healthy', latency_ms: 120 },
      ],
      overall_status: 'healthy',
    }
  },
  async getLiveLogs(_tail) {
    return { lines: ['[INFO] Container running normally', '[INFO] Worker idle polling'], total: 2 }
  },
}
