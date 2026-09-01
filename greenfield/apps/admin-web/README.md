# Casework Admin Web

Vue 3 + TypeScript + Vite 管理后台壳，覆盖 Greenfield Harness 的五个一级运营页面：

- 运行总览：Agent desired state、队列、指标、异常和近期运行
- Harness 配置：Root Agent、独立 Subagent、上下文和 Tool Loop 设置
- 处理规则：Webhook 条件、`human` aliases、回复开关和本地化 footer
- Skill 与工具：版本化 registry、typed contracts 和连接测试
- 工单运行：运行筛选、Todo / Tool / Evidence 摘要与详情抽屉

当前版本使用本地 typed mock data，不调用后端。沙箱 CPU、内存、超时与资源重试策略按产品固定决策以只读方式展示。

## 本地运行

```bash
pnpm install
pnpm dev
```

## 验证

```bash
pnpm test
pnpm build
```
