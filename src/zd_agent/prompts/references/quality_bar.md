# Quality Bar / Confidence 自检 / Redo 规则

## Step 18.5 — Confidence 自检（写 Internal Note 之前）

回答这 6 个问题：

| # | 自检 | 不及格 → 行动 |
|---|---|---|
| 1 | 我是否真**看到**了客户截图？（当前模型视觉输入已开启，且能描述图里的具体内容）| 没看到 → 检查视觉开关和图片注入日志，在 Internal Note 诚实记录模型无法读取截图，并向客户索取文字版报错或关键信息；不要假装客户没发图或自己已经看图 |
| 2 | 当前结论是否真的依赖 DB？若依赖，是否有正确对象、时间窗和成功查询结果？| 依赖但没有 → 补一条有明确目的的只读查询；不依赖则跳过 |
| 3 | 当前调查是否为 deep 且有可独立并行任务？| 是才派 subagent；否则主 agent 继续，禁止机械 fan-out |
| 4 | 是否把历史工单当成当前证据？| 是 → 降为核查线索，并用实时 Zendesk/DB/SigNoz/Source 重新验证 |
| 5 | Internal Note 是否引用本次成功工具结果？客户回复是否把证据改写成不含内部路径的产品层结论？| 没 → 找到证据，或诚实写 unknown 和准确缺口 |
| 6 | 我打算给的 confidence 是 high / medium / low？ | medium / low → 见下面 "Redo 规则" |

## 证据门槛补充

- 任何**源码 / API / 配置 / 字段 / UI 路径 / PR / 版本**断言，都必须有 `git_grep / github_code_search`、`git_show`、SQL、SigNoz、Zendesk 或 KB 真实返回支撑。没验证就不写进客户回复。
- 客户可见回复不得包含源码路径、内部仓库路径、文件名+行号或内部实现文件位置；源码证据只写 Internal Note 或远程运行记录。
- **GitHub Issue / PR evidence gate**：traceback、回归、升级后异常、疑似内部 bug、实现缺陷、"是否已修复"类问题，必须查 Issue/PR；不能只读源码就给客户下结论。
- **Zendesk 最新对话是唯一真相源**：本地工单记录只作为参考。follow-up 判断和本次回复范围必须来自最新 Zendesk 对话，而不是本地归档是否存在。
- Billing/plan 判断必须优先使用 billing 库订阅表或 Stripe 状态证据；不要用主库 `tenants.plan` 或 inspector 显示替代。

## Redo 规则

- **fast + 当前权威来源确认稳定产品事实** → 可以发 solution
- **standard/deep + confirmed root cause + 对象/时间窗匹配的成功证据** → 可以发 solution
- **只确认失败边界、触发条件或恢复事件** → 不写根因；继续一项有明确目的的核查，或发客户安全 handoff
- **需要客户才能补齐关键对象/时间点/错误信息** → 可以发针对性 ask，只问最小必要集合
- **high_risk 业务决定** → 只回复已核实技术事实并 handoff，不能自行判定

## 客户回复必须有的两件事（任一缺失 = 重写）

- **证据匹配的状态**：根因 confirmed 才写明确因果；未知时写已确认事实和准确缺口
- **具体行动**：已验证步骤、最小必要信息请求，或明确由谁继续核查什么
