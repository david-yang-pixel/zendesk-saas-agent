import type { NavItem } from './types'

export const navItems = [
  { label: '工单日志', path: '/runs', icon: 'runs', hint: '工单流水与实时控制台' },
  { label: '模型配置', path: '/model', icon: 'spark', hint: '模型切换与即时测速' },
  { label: '规则与安全', path: '/rules', icon: 'rules', hint: '模式开关与告警隔离' },
  { label: '工具中心', path: '/tools', icon: 'database', hint: 'MCP网格与在线调试' },
] as const satisfies readonly NavItem[]
