# 供 Agent 使用密钥（agent_secret.py · broker）

> 决策依据：`docs/decisions/ADR-001-agent-secret-access.md`、`docs/features/F001-agent-secret-access.md`
> 状态：**P0 已实现**（`list` / `enroll` / `whoami` / `policy` / `audit`）；P1 待做（`run` / `unlock` / MCP）。

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

## P1 预告（未实现）

`run --name <条目> --as-env KEY=... -- <cmd>`（注入 env + 输出二次脱敏 + 退出码透传）、
`unlock --ttl 600` / `lock`（本机 10 分钟窗口：只免 `require_approval`，不放宽范围）、
MCP 薄壳（`secret_list` / `secret_run` / `secret_audit`，`mcp --print-config` 输出 `.mcp.json` 片段）。