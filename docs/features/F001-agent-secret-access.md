---
feature_ids: [F001]
related_features: []
topics: [security, agent, secrets, mcp]
doc_kind: spec
created: 2026-10-22
---

# F001: 供 Agent 调用的密钥取用能力（get 不暴露，只暴露「用」）

> Status: spec | Owner: TBD（用户/CVO 拍板）

## Why

本技能现在只做「单向脱敏 + 可逆恢复」：`map.json` 是还原真相源，Agent 全程看不到明文。
但真实场景里 Agent 需要**使用**密钥（调 API、跑部署、连数据库）。若直接把 `.km/**` 给 Agent 读，
就落入 2026 年业界公认的四个泄漏点：

1. **上下文污染**：任何进入 LLM 上下文/会话日志的值 = 已公开（厂商日志、长期记忆、trace 全会留）。
2. **误提交**：Agent 顺手 `git add .` 把 `.env`/秘密文件带进仓库。
3. **Scope collapse**：一个文件塞满所有 key，Agent 只要读一次就等于拿到全部。
4. **Prompt injection → 凭据使用决策**：注入可让 Agent 以攻击者参数调用工具/读密钥库。
   （业界共识：凭据本身存得对，但「何时出示」的判断不确定 —— ReversingLabs/2026）

结论：**不要给 Agent「读密钥」的接口；只给它「用密钥」的接口**，并由技能侧做策略 + 审计。

## What

在本技能内新增一个**凭据代理（broker）**入口，Agent 只能通过它间接使用密钥：

### 接口（CLI 与 MCP 双层，同一实现）

```
python scripts/agent_secret.py list  --id <源id> [--tag llm]        # 只回 条目名/标签/敏感级（永不含值）
python scripts/agent_secret.py run   --id <源id> --name <条目名> \
        --as-env KEY=... -- <cmd...>                                # 值注入子进程 env，不经过模型上下文
python scripts/agent_secret.py lease --id <源id> --name <条目名> --ttl 600   # 一次性句柄（600 权限临时件，到期自动删）
```

- `run` 是**主形态**：对齐 `op run --` / `bws run --` / Bitwarden 新开源 `aac run --` / `vault agent` / `infisical run`。
  模型只看到命令行，看不到值；值随子进程退出即消失。
- **不提供 `get`/`show`/`env-dump`**（含 `--print`），从接口形状上排除「把值回给模型」。
- `run` 的 stdout/stderr **先过二次脱敏再回传**：用已有 `map.json` + `sanitize_lib`（`value_fp`/token）
  检测输出里是否出现刚注入的值或其分片 → 命中则替换为 token 并写审计告警（本项目独有优势，
  其他工具靠 `before_message_write` 红action，我们本来就有值指纹表）。

### 策略（deny-by-default）

`<km>/<id>/config/agent-access.json`（秘密材料目录内，不入 Git）：

```json
{
  "default": "deny",
  "callers": {
    "officeace/小通": {
      "allow_tags": ["llm", "dev"],        // 只按标签放行，不按「全部」
      "deny_names": ["bank-card:*", "id-cn:*", "master-password"],
      "ttl_sec": 600, "max_calls_per_hour": 20,
      "require_approval": ["llm-prod-key"]
    }
  }
}
```

- **deny 清单永远包含**：银行卡/身份证等 PII、`credential=false` 条目、主密码类；
- 高敏感条目 `require_approval` → Agent 拿不到，必须**用户本机一次性授权**（one-shot grant，等同
  macOS keychain 弹窗 / 1Password 审批）；这与现有「Bitwarden 主密码只在用户本机输入」的红线一致。

### 审计（可追溯，不含值）

`<km>/<id>/reports/secret-access.jsonl`：`ts / caller / action / entry_name / ttl / result(ok|denied|redacted_output) / cmd_hash`。
追加写、只读展示（`agent_secret.py audit --tail 50`）。

### 后端（值从哪来）

| 选项 | 说明 | 何时用 |
|---|---|---|
| `map.json`（已有） | 现成，零依赖 | P0 起步 |
| OS keychain（Windows DPAPI / Credential Manager） | 免主密码、OS 级隔离 | 单机、少条目 |
| Bitwarden Secrets Manager（`bws` + machine account access token） | 专为**非人类身份**设计：按 machine account 限权、可撤销、有审计 | 要长期/多 Agent/可撤销 |
| HashiCorp Vault（动态凭据）/ Infisical / Doppler | 5–15 分钟 TTL 动态凭据 | 企业级、多后端 |

后端可切换而**接口不变**：`agent_secret.py` 只是解析层，Agent 永远只看到 `run`。

### MCP 封装（薄壳）

把 `secret_list` / `secret_run` 注册为 MCP tools（**不注册** `secret_get`），
任何 MCP 客户端（OfficeAce / Claude Code / Cursor）都能调；策略与审计仍在技能侧，MCP 层不做判断。

## Acceptance Criteria

- [ ] **AC-1**：`list` 输出仅含条目名/标签/敏感级，任何路径下都不输出值（含 `--debug`）。
- [ ] **AC-2**：`run` 注入的值不出现在 stdout/stderr/参数回显/日志中；退出码透传。
- [ ] **AC-3**：`run` 输出若含刚注入的值（或其≥8字符分片）→ 被替换为 token，并记 `redacted_output` 审计。
- [ ] **AC-4**：策略 `default: deny`；未在 `allow_tags` 内 / 命中 `deny_names` / `require_approval` 的请求一律拒绝，
  且拒绝原因写入审计。
- [ ] **AC-5**：`ttl_sec` 到期后句柄失效（`lease` 临时件被删、后续使用报错）。
- [ ] **AC-6**：每次调用有一条审计；审计不含值；可 `audit --tail` 查看。

## Dependencies

- 已有：`paths.py` / `km_config.py`（目录与配置分层）、`map.json`、`sanitize_lib.py`（值指纹 + 替换）。
- 可选后端：`bws`（Bitwarden Secrets Manager）、DPAPI（Windows 自带）、`vault` CLI。
- 用户本机动作：审批/解锁窗口（同 Bitwarden 导入的地位）。

## Risk

- **同机隔离边界有限**：Agent 与技能同进程/同用户运行时，Agent 仍可能直接读文件系统。
  真正的隔离必须靠 **OS 层**（不同 OS 用户 + ACL、DPAPI 绑定、或 broker 以独立用户/服务运行）。
  因此 P0 定位是「**收口 + 审计 + 防误用**」，而非对抗已被完全控制的 Agent。
- **Prompt injection**：injection 可以让 Agent 发起 `run` —— 靠 deny-by-default + 标签白名单 + 审批 + 速率限制降低影响面，
  并用 AC-3 的输出脱敏防「借输出外泄」。
- **审计自身泄漏**：审计只存名字与结果，禁止存命令原文与输出。
- **接口膨胀**：一旦提供 `get`，整套设计失效 —— 必须写进 SKILL.md 红线。

## Open Questions

- Q1：`caller` 身份怎么定？`--caller <agentId>` 自报，还是绑定 OfficeAce 的 thread/agent 身份（server 解析）？
- Q2：是否需要「本机解锁窗口」（用户解锁一次，N 分钟内免审批）？窗口期内算不算一次授权？
- Q3：P0 只支持 `map.json` 后端，还是同时接 DPAPI？
- Q4：MCP 薄壳放在本技能，还是作为独立 MCP server 仓？

## 里程碑

| 阶段 | 内容 | 风险 |
|---|---|---|
| **P0** | `list` + 策略骨架 + 审计 + 文档红线（**无值出口**） | 零风险，可立即做 |
| **P1** | `run`（env 注入 + 输出二次脱敏 + 退出码）+ 本机一次性审批 | 中：需 AC-2/AC-3 测试 |
| **P2** | MCP 封装 + 后端切换（`bws` machine account / DPAPI），值从 `map.json` 迁出，技能只留引用 | 中高：引入外部依赖 |

## 业界对照（2026）

- **1Password**：Service Account（非人类身份）+ MCP server，值不进上下文；Vault 分层（Dev-AI-Agents / CI-Prod / Personal 永不暴露）。
- **Bitwarden**：Secrets Manager（`bws` + machine account access token，专供 CI/Agent）+ 新开源 **Agent Access（`aac run`）**，
  明确「不建议把凭据直接给 LLM」，推荐 `aac run` 环境注入。
- **HashiCorp Vault**：动态凭据（按需签发、自动过期）；**Agent Vault**（开源 proxy，拦截工具请求注入短时令牌）。
- **社区实践**：TTL 5–15 分钟、tool-call 边界授权、per-agent allowlist、one-shot grant、会话日志 redaction。