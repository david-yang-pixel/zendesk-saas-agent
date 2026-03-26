---
name: webhook-zendesk-event-plan
description: Zendesk Event Webhook 鉴权与过滤实施计划
created_at: 2026-03-26
---

# Zendesk Event Webhook Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将现有 `POST /webhook/zendesk` 替换为 Zendesk Event Webhook 协议，并支持通过环境变量配置请求头鉴权。

**Architecture:** 保持 `src/zd_agent/entry/webhook.py` 作为 HTTP 入口，在该文件中拆分出认证、事件过滤和工单号提取等小函数；配置仍由 `src/zd_agent/config.py` 的 `EnvSettings` 统一读取。只处理 `zen:event-type:ticket.created` 且 `detail.tags` 中存在完整 `enterprise` 的事件，不匹配时返回 `200 ignored`。

**Tech Stack:** Python 3.12、aiohttp、pydantic-settings、unittest

---

### Task 1: Webhook 失败测试

**Files:**
- Create: `tests/test_webhook.py`
- Test: `tests/test_webhook.py`

- [ ] **Step 1: 写 webhook 行为测试**

覆盖成功派发、鉴权失败、事件类型不匹配、缺少 `enterprise` 标签、工单号非法五类场景。

- [ ] **Step 2: 运行测试并确认失败**

Run: `uv run python -m unittest tests.test_webhook -v`
Expected: FAIL，提示 webhook 入口尚未支持新的鉴权与事件过滤行为。

### Task 2: 实现环境变量认证与 Zendesk 事件过滤

**Files:**
- Modify: `src/zd_agent/config.py`
- Modify: `src/zd_agent/entry/webhook.py`
- Test: `tests/test_webhook.py`

- [ ] **Step 1: 新增环境变量配置**

在 `EnvSettings` 中新增：
- `zd_webhook_auth_header`
- `zd_webhook_auth_value`

- [ ] **Step 2: 实现最小 webhook 逻辑**

在 `src/zd_agent/entry/webhook.py` 中实现：
- 读取环境变量并校验 header
- 校验事件类型是否为 `zen:event-type:ticket.created`
- 校验 tags 中是否存在完整 `enterprise`
- 从 `detail.id` 提取工单号并派发
- 不匹配时返回 `200 ignored`

- [ ] **Step 3: 运行测试确认通过**

Run: `uv run python -m unittest tests.test_webhook -v`
Expected: PASS

### Task 3: 回归验证

**Files:**
- Modify: `src/zd_agent/entry/webhook.py`
- Modify: `src/zd_agent/config.py`
- Test: `tests/test_webhook.py`

- [ ] **Step 1: 运行相关测试**

Run: `uv run python -m unittest tests.test_webhook tests.test_source_selector tests.test_official_docs -v`
Expected: PASS

- [ ] **Step 2: 检查静态告警**

Run IDE lints for changed files and fix any new issues that are easy to resolve.
