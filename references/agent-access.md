# 供 Agent 使用密钥（agent_secret.py · broker）

> 决策依据：`docs/decisions/ADR-001-agent-secret-access.md`、`docs/features/F001-agent-secret-access.md`
> 状态：**P0 + P1 + P2 已实现**（CLI：`list` / `enroll` / `whoami` / `policy` / `audit` / `run` / `unlock` / `lock` / `mcp`；MCP 薄壳 `scripts/agent_secret_mcp.py`）。

## 为什么这样设计（一句话）

Agent 需要的不是「读密钥」，而是「**用**密钥」。任何进入模型上下文的值都等于已公开，
所以本技能**永不提供读值接口**，只提供「把值注入子进程」的通道，并由技能侧做授权 + 审计。

## 红线（写死在 SKILL.md，勿破）

1. **没有「读值」出口**：`agent_secret.py` 任何子命令都不打印条目明文；历史上也不得新增 `get`/`show`/`--print`。
2. **值永不回模型上下文**：P1 的 `run` 只把值注入**子进程环境变量**，随子进程退出即消失。
3. **deny-by-default**：未注册 = `anonymous`，只能读 `public_tags` 的元数据，**不得 run**。
4. **caller 身份 = 持有 token**（`AGENT_SECRET_TOKEN`）；token 只签发时显示一次，落盘只存 `sha256`。
5. **审计不含值**、不含命令原文。
6. 本机专属动作（`enroll` / P1 的 `unlock`）**只能由用户执行**——同 Bitwarden 主密码的地位。

## 快速开始

```bash
# 1) 用户本机（一次）：签发 caller token —— token 只显示这一次，交给 Agent 存进它自己的密钥库/环境变量
python scripts/agent_secret.py enroll --id <源id> --agent claude-code --tags llm-key,secret
#    → 得到 agt_xxxxxxxx...（44 字符）

# 2) Agent 侧（只需一个环境变量，任何框架通用）
export AGENT_SECRET_TOKEN=agt_xxxxxxxx...        # PowerShell: setx AGENT_SECRET_TOKEN "..."
#    或逐次传：--caller-token agt_...

# 3) 查看自己是谁、能看什么
python scripts/agent_secret.py whoami --id <源id>
python scripts/agent_secret.py list   --id <源id>            # 只有元数据：名/标签/凭据位/敏感级
python scripts/agent_secret.py audit  --id <源id> --tail 50  # 访问审计
```

**目录口径**：`--id <源id>` → `<秘密材料根>/<id>`（秘密材料根 = `--secrets-root` 或 `settings.secrets_root` 或 `<技能目录>/.km`）；
`--data-root <目录>` 直接指秘密材料目录（与 `sanitize.py` / `backfill.py` 等口径一致）。两者互斥。

## 命令速查

| 命令 | 谁执行 | 作用 |
|---|---|---|
| `list [--tag X] [--json]` | Agent | 列条目**元数据**（永不含值）；按 caller 授权过滤 |
| `whoami` | Agent | 打印 caller 解析结果（principal / 通道 / 依据），**不打印 token** |
| `policy --action list\|run --name <条目>` | Agent/运维 | 干跑授权判定，打印 `allow/deny + 原因`（无副作用） |
| `audit --tail N` | Agent/运维 | 查看 `reports/secret-access.jsonl` |
| `enroll --agent X --tags a,b [--ttl 600] [--max-calls 20] [--require-approval p] [--revoke] [--force]` | **用户本机** | 签发/吊销 caller token |
| `run --name <条目> --as-env KEY -- <cmd...>` | Agent | ★把值注入**子进程 env** 执行命令；输出二次脱敏；退出码透传（拒绝=13） |
| `unlock --ttl 600 --yes [--scope p1,p2]` | **用户本机** | 开启解锁窗口（默认 600s=10 分钟；上限 3600s） |
| `lock` | **用户本机** | 提前关闭解锁窗口 |

## 文件（都在秘密材料目录内，永不外传）

```
<秘密材料根>/<id>/
├── config/agent-access.json        # 全局策略（default=deny / public_tags / deny_labels / deny_names / unlock_ttl_sec=600）
├── config/agents/<agent>.json      # 每 caller：token_sha256 + 末4位提示 + allow_tags + 限额 + 吊销标记（**不存明文 token**）
└── reports/secret-access.jsonl     # 审计：ts/principal/channel/action/entry/result/reason/ttl/cmd_hash（不含值）
```

`agent-access.json`（首次 `enroll` 自动生成骨架）：

```json
{
  "version": 1,
  "default": "deny",
  "public_tags": [],
  "deny_labels": ["bank-card", "id-cn"],
  "deny_names": [],
  "unlock_ttl_sec": 600,
  "require_credential": true
}
```

判定次序（`judge()`，`list` 与未来的 `run` 共用同一入口）：
**全局 `deny_labels`/`deny_names` → anonymous（只 public + 不得 run）→ agent 吊销/停用 → agent `deny_*` → `allow_tags` → （run）`credential=true` → （run）`require_approval`**。

## 与 Bitwarden 导入的分工

- 本 broker（P0/P1）：Agent **在线用**密钥，值只注入子进程；后端 = `map.json`。
- `bitwarden-import.ps1`（★用户本机）：把凭据**归档进 Bitwarden**，供人查阅/长期保存。
- 两者都**不把值交给 Agent 上下文**；`map.json` 是恢复真相源，恢复**不依赖 Bitwarden**。

## 已验证（P0 自测证据）

- 匿名：`list` 放行 0 / 拒绝全部；`policy run` 一律 deny；错误 token / 已吊销 token → `anonymous`。
- 真实库（272 条目）**最坏情况渲染**（给一个能看全部标签的 caller）：放行 262 / 拒绝 10（= 9 `bank-card` + 1 `id-cn`），
  对 `map.json` 全部 272 个真值反查 **命中 0**；`--json` 视图同样 0。
- 脚本内 `value` 只有一处引用（`_resolve_value()`，P1 `run` 预留），CLI 无任何读值路径。

## P1（已实现）：run / unlock / lock

```bash
# Agent：把值注入子进程 env（值绝不回传模型）
python scripts/agent_secret.py run --id <源id> --name "[REDACTED:llm-key:xxxx]" \
    --as-env OPENAI_API_KEY -- <命令...>
#   可选 --as-file <路径>：改注入到一个 600 权限临时文件（子进程退出后**先清零再删**）

# ★用户本机：10 分钟解锁窗口（只免 require_approval；须 --yes）
python scripts/agent_secret.py unlock --id <源id> --ttl 600 --yes [--scope "p1,p2"]
python scripts/agent_secret.py lock --id <源id>          # 提前关闭
```

- **退出码**：子进程退出码原样透传；**策略拒绝 = 13**；用法/内部错误 = 2。
- **输出二次脱敏**：子进程 stdout/stderr 会先与 `map.json` 全部真值比对（不止本次注入的那个），
  命中的替换为对应 token；另对**本次注入值**做 ≥8 字符分片兜底 → 审计记 `redacted_output`，并打印 `[已拦截]`。
- **限额**：`max_calls_per_hour`（每 caller）按审计统计近 1 小时**成功**调用数，超限即拒绝（`denied` 不计入）。
- **窗口语义**：窗口**只**免 `require_approval`；`allow_tags` / `deny_*` / 限额 / 审计全部照旧；
  到期（`expires_at_epoch`）自动失效，`lock` 立即失效。`unlock` 必须带 `--yes`（本机人工确认），且 `--ttl` 上限 3600s。
- **匹配语义**：`--require-approval` / `--deny-names` / `--scope` 的模式只把 `*` `?` 当通配，其余字符（含 `[]`）按字面
  —— 因为条目名形如 `[REDACTED:label:hex]`，用 `fnmatch` 会把 `[...]` 误当字符类（**已修，见下方教训**）。
- **已知限制（不设防）**：子进程若自行把值写盘/发网络，本层拦不住 —— 同机隔离上限见 ADR Risk；本能力定位是
  **收口 + 授权 + 审计 + 防误用**。

## P2（已实现，**可选、非主流程**）：MCP 薄壳

> **主流程用 CLI，不要求、也不强制配置 MCP**（CVO 2026-10-22 定：Agent 不需要持有值，能用即可）。
> 本节仅在「客户端是 MCP 原生、且不想给 Agent shell 权限」时才需要。

**技能是唯一入口**：你不需要自己管 MCP——让技能生成配置：

```bash
python scripts/agent_secret.py mcp --id <源id>     # 打印可直接粘贴的 .mcp.json 片段
```

生成的片段（`command` 用当前解释器、`args` 带 `--id`、`env` 只留 `AGENT_SECRET_TOKEN` 占位）：

```json
{"mcpServers": {"agent-secret": {
  "command": "<python>",
  "args": ["<abs>/scripts/agent_secret_mcp.py", "--id", "<源id>"],
  "env": {"AGENT_SECRET_TOKEN": "${AGENT_SECRET_TOKEN}"}}}}
```

- **传输**：stdio / JSON-RPC 2.0（一行一条消息）；方法支持 `initialize` / `tools/list` / `tools/call` / `ping`。
- **只暴露 3 个工具，没有 `secret_get`**：
  - `secret_list`（`tag?` / `limit?`）→ 元数据（名/标签/凭据位/敏感级），按 caller 授权过滤，**永不含值**。
  - `secret_run`（`name` / `cmd[]` / `as_env?`）→ 返回**已二次脱敏**的 `stdout` / `stderr` / `exit_code` / `redacted` 数；越权/超限 → `isError=true`。
  - `secret_audit`（`tail?`）→ 结构化审计尾部（不含值）。
  - 显式调用 `secret_get`/`secret_show`/`secret_read`/`secret_value` → **明确拒绝**（红线）。
- **与 CLI 共用内核不分叉**：MCP 直接 `import agent_secret`，调 `run_secret()` + `judge()` + `audit_rows()`；策略 / 窗口 / 限额 / 审计完全一致，`channel=mcp` 仅作审计标签。
- 身份只从环境变量 `AGENT_SECRET_TOKEN` 取（由 MCP 客户端配置注入），MCP 进程不管理凭据。

**已测证据（2026-10-22）**：合成库 `echo` 模式（子进程把明文打到 stdout）→ 响应里 `stdout` 已变成 `[REDACTED:secret:ccccdddd]`、`redacted=1`、**全响应无明文**；`tools/list` 恰 3 个工具且无 `secret_get`；调用 `secret_get` → `isError=true` 拒绝；审计出现 `channel=mcp` 行。真实库 `office`（长度模式，避免回传真值）同样全绿。

## 自测过程中修掉的 bug（教训）

- **`fnmatch` 把 `[REDACTED:...]` 当字符类** → `--require-approval` 永不命中，窗口形同虚设（红灯：窗口外仍 allow）。
  改为自写 `name_match()`：仅 `*`/`?` 是通配，其余 `re.escape` 后按字面匹配。**同类模式匹配都要遵此**。