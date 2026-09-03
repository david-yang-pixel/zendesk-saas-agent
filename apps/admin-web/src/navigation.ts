import type { NavItem } from './types'

export const navItems = [
  { label: '运行总览', path: '/', icon: 'overview', hint: '服务大盘与核心指标' },
  { label: '工单推演', path: '/runs', icon: 'runs', hint: 'AI思考链与工具调用回放' },
  { label: '模型配置', path: '/harness', icon: 'spark', hint: '模型网关与实时测速' },
  { label: 'MCP 总线', path: '/mcp', icon: 'database', hint: '7大服务与工具网格' },
  { label: '技能中心', path: '/skills', icon: 'skills', hint: 'SOP与排查知识库' },
  { label: '准入规则', path: '/rules', icon: 'rules', hint: '人工保护与过滤策略' },
] as const satisfies readonly NavItem[]
