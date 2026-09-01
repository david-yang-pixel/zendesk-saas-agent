export type AgentDesiredState = 'running' | 'paused'
export type StatusTone = 'success' | 'warning' | 'danger' | 'neutral' | 'info'
export type RunStatus = 'running' | 'awaiting_customer' | 'completed' | 'human_takeover' | 'failed'

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
  readonly steps: number
}

export interface HarnessConfig {
  rootModel: string
  rootProvider: string
  subagents: Array<{
    id: string
    name: string
    purpose: string
    model: string
    provider: string
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
  readonly question: string
  readonly todoCompleted: number
  readonly todoTotal: number
  readonly toolCalls: number
  readonly subagents: number
  readonly evidence: number
  readonly checkpoint: string
  readonly sandbox: 'destroyed' | 'running'
  readonly outcome: string
}
