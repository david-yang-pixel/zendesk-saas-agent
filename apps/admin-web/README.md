# Casework Admin Web

Vue 3 + TypeScript + Vite 管理后台壳，覆盖 Greenfield Harness 的五个一级运营页面：

- 运行总览：Agent desired state、队列、指标、异常和近期运行
- Harness 配置：Root Agent、独立 Subagent、上下文和 Tool Loop 设置
- 处理规则：Webhook 条件、`human` aliases、回复开关和本地化 footer
- Skill 与工具：版本化 registry、typed contracts 和连接测试
- 工单运行：运行筛选、Todo / Tool / Evidence 摘要与详情抽屉

页面通过版本化 Admin API 读取运行总览、Harness 配置、处理规则、能力注册表和运行记录。配置保存携带 `expected_revision`，发生并发修改时显示冲突并要求重新加载；凭据只使用后端引用，不在浏览器处理明文。沙箱 CPU、内存、超时与资源重试策略按产品固定决策以只读方式展示。

开发和测试时可通过 `AdminApiTransport` 注入内存实现，页面测试不依赖真实网络。开发默认使用同源 `/api/*`；生产构建默认挂载在 `/admin/`，因此浏览器 transport 会请求 `/admin/api/*`。可用 `ADMIN_WEB_BASE_PATH` 覆盖生产挂载前缀，例如 `ADMIN_WEB_BASE_PATH=/console/ pnpm build`。SPA history 路由会使用相同前缀。

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
