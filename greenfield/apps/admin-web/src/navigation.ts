import type { NavItem } from './types'

export const navItems = [
  { label: '运行总览', path: '/', icon: 'overview', hint: '智能客服与队列状态' },
  { label: 'Agent 配置', path: '/harness', icon: 'harness', hint: '模型、上下文与沙箱' },
  { label: '处理规则', path: '/rules', icon: 'rules', hint: '入口过滤与回复策略' },
  { label: 'Skill 与工具', path: '/skills', icon: 'skills', hint: '能力注册与连接状态' },
  { label: '工单运行', path: '/runs', icon: 'runs', hint: '会话、证据与结果' },
] as const satisfies readonly NavItem[]
