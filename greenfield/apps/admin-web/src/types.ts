export type AgentDesiredState = 'running' | 'paused'
export type StatusTone = 'success' | 'warning' | 'danger' | 'neutral' | 'info'
export type RunStatus = 'running' | 'awaiting_customer' | 'completed' | 'human_takeover' | 'failed' | 'cancelled'

export interface NavItem {
  readonly label: string
  readonly path: string
  readonly icon: IconName
  readonly hint: string
}

export type IconName =
  | 'overview'
  | 'harness'
  | 'rules'
  | 'skills'
  | 'runs'
  | 'chevron'
  | 'external'
  | 'bell'
  | 'search'
  | 'refresh'
  | 'play'
  | 'pause'
  | 'check'
  | 'alert'
  | 'clock'
  | 'sliders'
  | 'database'
  | 'box'
  | 'spark'
  | 'shield'
  | 'more'
  | 'arrow-up'
  | 'arrow-down'
  | 'activity'
  | 'terminal'
  | 'download'
  | 'logout'

export interface MetricCard {
  readonly label: string
  readonly value: string
  readonly detail: string
  readonly trend?: string
  readonly trendTone?: StatusTone
  readonly icon: IconName
  readonly accent: 'blue' | 'green' | 'amber' | 'violet'
}

export interface QueueHealth {
  readonly label: string
  readonly count: number
  readonly description: string
  readonly tone: StatusTone
}

export interface FailureItem {
  readonly id: string
  readonly title: string
  readonly detail: string
  readonly time: string
  readonly tone: StatusTone
}

export interface RecentRun {
  readonly ticketId: number
  readonly subject: string
  readonly status: RunStatus
  readonly statusLabel: string
  readonly startedAt: string
  readonly duration: string
  readonly steps: number | null
}

export interface HarnessConfig {
  rootModel: string
  rootProvider: string
  rootBaseUrl: string
  rootCredentialRef: string
  subagents: Array<{
    id: string
    name: string
    purpose: string
    model: string
    provider: string
    baseUrl: string
    credentialRef: string
    contextWindowTokens: number
    maxToolRounds: number
    maxParallel: number
    enabled: boolean
  }>
  contextWindow: string
  softCompaction: number
  hardCheckpoint: number
  sandboxCpu: string
  sandboxMemory: string
  timeoutMinutes: number
  maxRetries: number
  maxToolRounds: number
}

export interface ModelRoutingConfig {
  provider: string
  baseUrl: string
  model: string
  credentialRef: string
}

export interface SubagentSlotConfig extends ModelRoutingConfig {
  slotId: string
  enabled: boolean
  maxParallel: number
  contextWindowTokens: number
  maxToolRounds: number
}

export interface AdminConfig {
  desiredState: AgentDesiredState
  rootModel: ModelRoutingConfig
  subagentSlots: SubagentSlotConfig[]
  rules: ProcessingRules
  enabledSkills: string[]
  enabledTools: string[]
  contextWindowTokens: number
  softCompactionPercent: number
  hardCheckpointPercent: number
  maxToolRounds: number
}

export interface AdminConfigSnapshot {
  revision: string
  config: AdminConfig
  createdAt: string
  actor: string
}

export interface SandboxPolicy {
  attempt1: { cpu: number; memoryMib: number; timeoutSeconds: number }
  attempt2: { cpu: number; memoryMib: number; timeoutSeconds: number }
  retryCondition: string
}

export interface ModelCredentialBinding {
  readonly provider: string
  readonly baseUrl: string
  readonly model: string
  readonly credentialRef: string
}

export interface ModelCredentialStatus {
  readonly credentialRef: string
  readonly configured: boolean
  readonly maskedValue: string | null
  readonly updatedAt: string | null
  readonly bindings: ModelCredentialBinding[]
}

export interface ModelCredentialProbe {
  readonly status: 'connected' | 'degraded'
  readonly latencyMs: number | null
  readonly errorCode: string | null
}

export interface ModelCredentialReplaceResult {
  readonly credential: ModelCredentialStatus
  readonly probe: ModelCredentialProbe
}

export interface ModelCatalog {
  readonly provider: string
  readonly baseUrl: string
  readonly models: string[]
  readonly latencyMs: number
}

export interface OverviewData {
  desiredState: AgentDesiredState
  configRevision: string
  heartbeat: string | null
  metrics: MetricCard[]
  queueHealth: QueueHealth[]
  failures: FailureItem[]
  recentRuns: RecentRun[]
}

export interface RunListData {
  items: RunRecord[]
  total: number
}

export interface RegistryData {
  skills: SkillRecord[]
  tools: ToolRecord[]
}

export interface ConnectionProbe {
  toolId: string
  status: 'connected' | 'degraded' | 'untested'
  latencyMs: number | null
  errorCode: 'timeout' | 'unavailable' | 'not_configured' | 'unknown_tool' | null
  detail: string | null
}

export interface ProcessingRules {
  saasTag: string
  assignee: string
  statuses: string[]
  humanAliases: string[]
  replyEnabled: boolean
  footerLocale: string
  footers: Record<string, string>
}

export interface SkillRecord {
  readonly id: string
  readonly name: string
  readonly description: string
  readonly version: string
  readonly assignedTodos: string
  readonly status: 'enabled' | 'core' | 'disabled'
  readonly updatedAt: string
}

export interface ToolRecord {
  readonly id: string
  readonly name: string
  readonly description: string
  readonly scope: 'root + subagents' | 'root only'
  readonly contract: string
  readonly connection: 'connected' | 'degraded' | 'untested'
  readonly latency: string
}

export interface RunRecord extends RecentRun {
  readonly sessionId: string
  readonly eventId: string | null
  readonly expectedEventId: string | null
  readonly attempts: number
  readonly configRevision: string | null
  readonly question: string | null
  readonly todoCompleted: number | null
  readonly todoTotal: number | null
  readonly toolCalls: number | null
  readonly subagents: number | null
  readonly evidence: number | null
  readonly checkpoint: string | null
  readonly sandbox: 'destroyed' | 'running'
  readonly outcome: string
  readonly failureReason: string | null
  readonly retryClassification: string | null
  readonly snapshotCursor: string | null
}
