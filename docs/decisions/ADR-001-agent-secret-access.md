---
decision_id: ADR-001
feature_ids: [F001]
topics: [security, agent, secrets, mcp, decision]
doc_kind: decision
status: accepted
created: 2026-10-22
---

# ADR-001: Agent 密钥取用能力——caller 身份、解锁窗口、后端与 MCP 归属

> Status: accepted | Decided by: 用户/CVO（2026-10-22 21:39）| Feature: F001

承接 [F001](../features/F001-agent-secret-access.md) 的四个待决问题。用户裁决 + 细化设计如下。

---

## D1 · caller 身份：**两段式**——「主体凭 token（可验证）」+「通道仅作提示（可伪造）」

### 问题本质

Agent 是**无人值守的非人类主体**，传统的「用户名 / 进程名 / 会话」都**不可信**（谁都能改进程名、设环境变量）。
所以身份不能靠「自报」，只能是**持有秘密**（token）。但要给「各 Agent 尽量通用」，就不能绑定某个框架。

### 设计：主体 token + 通道标签，两段分离

| 段 | 是什么 | 来源（通用性） | 可信度 | 用途 |
|---|---|---|---|---|
| **主体身份 principal** | 代表哪个 Agent | **caller token**（持有即身份） | 高（秘密） | 授权决策（policy 绑定 principal） |
| **通道身份 channel** | 从哪条路进来 | CLI 子进程 / MCP client / 文件读取 | 低（可伪造） | **仅写审计**，不参与授权 |

**授权只看 principal；channel 只记录**。这样任何 Agent（MCP 客户端、能跑命令的脚本、CI、编辑器插件）都能接入，
且不用信任它的「自称」。

### 通用识别链（按优先级，第一条命中即用）

1. `--caller-token <t>` 命令行参数（显式，最通用）
2. 环境变量 **`AGENT_SECRET_TOKEN`**（**约定主通道**：任何框架只需设一个环境变量即接入）
3. `--caller-token-stdin` / 从 stdin 读（避免进程列表泄露 token）
4. 未命中 → `anonymous`（受限角色，见下）

> 增强项（**可选，不是必需**，缺失不影响工作）：`--caller` / `AGENT_ID` 自报名（仅作审计提示，**绝不参与授权**）；
> MCP `clientInfo.name`、OfficeAce 的 thread/agent 身份、CI 的 workflow id —— 一律降级为「通道标签/审计字段」。
> 这样设计保证：**离线、无 MCP、无宿主支持时，仅靠一个环境变量也能全功能使用**。

### 注册（enroll）：用户本机一次，agent 只拿 token

```bash
python scripts/agent_secret.py enroll --id <源id> --agent "claude-code" --tags llm,dev --ttl 600
# 打印一次 token（形如 agt_<40随机>，仅此一次，不可再取）→ 交给该 Agent 存进它自己的 keychain/env
```

- 落盘 `<km>/<id>/config/agents/<agent>.json`：**只存 `token_sha256` 前缀指纹 + 角色/标签/TTL/限额**，永不存明文 token。
- 校验：`sha256(候选 token)` 与注册表**常量时间比较**；未注册 → `anonymous`。
- token 语义对齐 Bitwarden access token / 1Password Service Account：**只在签发时显示一次，可单独吊销**（`enroll --revoke`）。
- Agent 侧落地方式（尽量通用）：环境变量 `AGENT_SECRET_TOKEN`，或写进它自己的密钥库；**不要写进仓库**。

### `anonymous` 角色（未注册兜底）

`default: deny`。未注册只允许**只读元数据**（`list`），且仅限 policy 标 `public: true` 的标签；
**`run` 一律拒绝**。→ 新 Agent 想用值，必须先经用户 `enroll`（明确授权动作）。

### 审计字段

每条记录 `ts / principal / channel / action / entry_name / result / ttl / cmd_hash`。
`principal=anonymous` 或 token 校验失败会**单独告警**（可用于发现乱接入的 Agent）。

### 为什么这样最通用

- 身份不依赖：框架、进程名、用户名、是否支持 MCP、是否在 OfficeAce 里。
- 接入成本 = **设一个环境变量**（或加一个 `--caller-token`）。
- 授权可审计、可吊销、可分级（每 Agent 不同标签/TTL/限额）。

---

## D2 · 解锁窗口：**10 分钟（600s）**，由用户本机开启

```bash
python scripts/agent_secret.py unlock --ttl 600      # 本机交互执行；默认 600s = 10 分钟
python scripts/agent_secret.py lock                  # 提前关闭
```

- 落盘 `<km>/<id>/state/unlock.json`：`{expires_at, scope, by}`（`scope` = 放行哪些 `require_approval` 条目）。
- **窗口内**：`run` 对 `require_approval` 条目**免逐次审批**。
- **窗口外**：命中 `require_approval` 一律拒绝并回「请在本机执行 unlock」。
- 窗口内**仍然受**：`allow_tags` + `deny_names` + 限额（`max_calls_per_hour`）+ 全量审计。**窗口只免审批，不放宽范围**。
- `unlock` 必须**用户本机交互**（Agent 不得自行 unlock；同「Bitwarden 主密码只在用户本机输入」红线）。
- 到期自动失效；一次 10 分钟 ≈ 一个任务窗口。

---

## D3 · 后端：**只支持 `map.json`**（P0/P1），保留抽象位

- 值只从 `<km>/<id>/vault/map/map.json` 解析。不引入 DPAPI / `bws` / Vault。
- `agent_secret.py` 内**单一入口 `resolve_value(name)`**，后续换后端只改这一处，接口与策略不变。
- 收益：零外部依赖、零新增密钥材料、当场可测；代价：后端与脱敏真相源强耦合（接受）。

---

## D4 · MCP：**放本技能，技能是唯一入口，Agent 无需管理 MCP**

- MCP server 实现**在本技能仓内**（`scripts/agent_secret_mcp.py`），与 CLI **共用同一策略/审计内核**（一份授权逻辑，避免两套漂移）。
- 暴露 MCP tools：`secret_list` / `secret_run` / `secret_audit`（只读）。**不暴露 `secret_get`**。
- **技能知道怎么调用自己**：`agent_secret.py mcp --print-config` 输出可直接粘贴的 `.mcp.json` 片段；
  `SKILL.md` 明文写「需要密钥时用 `secret_run`（MCP）或 `agent_secret.py run`（CLI）」。
- **技能作为统一入口**：Agent 只加载技能 → 技能文档告诉它怎么取用 → 是否启用 MCP 由宿主/用户决定；
  **未启用 MCP 时 CLI 仍全功能可用**（MCP 是可选薄壳，不是必需件）。
- Agent 不需要管理 MCP（server 实现 + 配置片段都由技能提供）。

---

## 红线（不因本 ADR 放宽）

1. 主密码 / `BW_SESSION` / 任何 caller token 明文**永不进 Agent 上下文**；
2. 值**永不回模型上下文**——只注入子进程 env 或临时句柄；
3. **deny-by-default**；`run` 的输出先过二次脱敏（`map.json` 值指纹）再回传；
4. 审计**不含值**、不含命令原文；
5. 本机专属动作（enroll / unlock / Bitwarden 导入）**只能由用户执行**；
6. Agent 与技能同机同用户时，真正隔离靠 OS 层（独立用户 + ACL / DPAPI）——本能力定位是**收口 + 授权 + 审计 + 防误用**。

## Consequences

- ➕ 任意 Agent 一个环境变量即接入；授权可分级、可吊销、可审计。
- ➕ CLI 为主入口 → 不依赖 MCP 生态即可用；MCP 只是额外通道。
- ➖ `map.json` 后端 = 后端与脱敏真相源耦合（D3 已接受）。
- ➖ 同机同用户的隔离上限（见红线 6）。

## 关联

- Feature：`docs/features/F001-agent-secret-access.md`
- 相关：`references/security.md`（安全模型）、`scripts/sanitize_lib.py`（值指纹/替换，供 D-红线3 复用）