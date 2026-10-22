---
feature_ids: [F001]
related_features: []
topics: [security, agent, secrets, mcp]
doc_kind: spec
created: 2026-10-22
---

# F001: 供 Agent 调用的密钥取用能力（get 不暴露，只暴露「用」）

> Status: spec（决策已定，见 [ADR-001](../decisions/ADR-001-agent-secret-access.md)）| Owner: 用户/CVO

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

在本技能内新增一个**凭据代理（broker）**入口，Agent 只能通过它间接使用密钥。
**CLI 是第一入口（任何能跑命令/设环境变量的 Agent 都能用），MCP 是可选薄壳**。

### 接口（CLI 与 MCP 双层，共用同一策略/审计内核）

```
# —— Agent 侧（取用）——
python scripts/agent_secret.py list  --id <源id> [--tag llm]        # 只回 条目名/标签/敏感级（永不含值）
python scripts/agent_secret.py run   --id <源id> --name <条目名> \
        --as-env KEY=... -- <cmd...>                                # 值注入子进程 env，不经过模型上下文
python scripts/agent_secret.py audit --id <源id> --tail 50          # 只读审计

# —— 用户本机侧（授权）——
python scripts/agent_secret.py enroll --id <源id> --agent <名> --tags llm,dev --ttl 600   # 签发 caller token（仅显示一次）
python scripts/agent_secret.py enroll --id <源id> --revoke <名>                           # 吊销
python scripts/agent_secret.py unlock --id <源id> --ttl 600                               # 本机解锁窗口（默认 600s）
python scripts/agent_secret.py lock                                                        # 提前关闭窗口
python scripts/agent_secret.py mcp --print-config                                          # 输出可直接粘贴的 .mcp.json 片段

# —— MCP tools（可选通道）——
secret_list / secret_run / secret_audit        # 注意：不注册 secret_get
```

- `run` 是**主形态**：对齐 `op run --` / `bws run --` / Bitwarden 新开源 `aac run --` / `vault agent` / `infisical run`。
  模型只看到命令行，看不到值；值随子进程退出即消失。
- **不提供 `get`/`show`/`env-dump`**（含 `--print`），从接口形状上排除「把值回给模型」。
- `run` 的 stdout/stderr **先过二次脱敏再回传**：用已有 `map.json` + `sanitize_lib`（`value_fp`/token）
  检测输出里是否出现刚注入的值或其分片 → 命中则替换为 token 并写审计告警（本项目独有优势，
  其他工具靠 `before_message_write` 红action，我们本来就有值指纹表）。

### caller 身份（ADR-001 D1：两段式——token 定主体，通道只作提示）

- **主体**由**持有 caller token** 决定（持有即身份，可验证）；**通道**（cli/mcp/ci）仅写审计，**不参与授权**。
- 通用识别链：`--caller-token` → 环境变量 **`AGENT_SECRET_TOKEN`**（约定主通道）→ stdin → `anonymous`。
- `enroll` 时**只打印一次** token，落盘只存 `sha256` 指纹；校验用常量时间比较；未注册 = `anonymous`。
- `anonymous`：`default: deny`，只允许 `list` 的 `public` 标签元数据，**`run` 一律拒绝**。

### 策略（deny-by-default）

`<km>/<id>/config/agent-access.json`（秘密材料目录内，不入 Git）：

```json
{
  "default": "deny",
  "callers": {
    "claude-code": {
      "allow_tags": ["llm", "dev"],        // 只按标签放行，不按「全部」
      "deny_names": ["bank-card:*", "id-cn:*", "master-password"],
      "ttl_sec": 600, "max_calls_per_hour": 20,
      "require_approval": ["llm-prod-key"]
    }
  }
}
```

- **deny 清单永远包含**：银行卡/身份证等 PII、`credential=false` 条目、主密码类；
- 高敏感条目 `require_approval` → Agent 拿不到，须**用户本机 `unlock` 10 分钟窗口（ADR-001 D2）**；
  窗口内免逐次审批，但 `allow_tags`/`deny_names`/限额/审计**全部照旧**；窗口外一律拒绝。
  这与现有「Bitwarden 主密码只在用户本机输入」的红线一致。

### 审计（可追溯，不含值）

`<km>/<id>/reports/secret-access.jsonl`：`ts / principal / channel / action / entry_name / ttl / result(ok|denied|redacted_output) / cmd_hash`。
追加写、只读展示（`agent_secret.py audit --tail 50`）；`principal=anonymous` 或 token 校验失败单独告警。

### 后端（值从哪来）

**P0/P1 只支持 `map.json`（ADR-001 D3）**：值只从 `<km>/<id>/vault/map/map.json` 解析，不引入 DPAPI/`bws`/Vault；
`agent_secret.py` 内保留单一入口 `resolve_value(name)`，后续换后端只改这一处。

> 演进备选（不在本 Feature 范围）：OS keychain/DPAPI、Bitwarden Secrets Manager（`bws` machine account）、HashiCorp Vault。

### MCP 封装（ADR-001 D4：本技能自带薄壳，技能为唯一入口）

- 实现在本仓 `scripts/agent_secret_mcp.py`，与 CLI **共用同一策略/审计内核**（一份授权逻辑）。
- 暴露 `secret_list` / `secret_run` / `secret_audit`，**不暴露 `secret_get`**。
- **技能知道怎么调自己**：`SKILL.md` 写明调用方式；`mcp --print-config` 输出 `.mcp.json` 片段。
- **技能作为统一入口**：Agent 只加载技能即可；启用 MCP 由宿主/用户决定，**未启用时 CLI 全功能可用**。
  Agent 无需管理 MCP。

## Acceptance Criteria

- [ ] **AC-1**：`list` 输出仅含条目名/标签/敏感级，任何路径下都不输出值（含 `--debug`）。
- [ ] **AC-2**：`run` 注入的值不出现在 stdout/stderr/参数回显/日志中；退出码透传。
- [ ] **AC-3**：`run` 输出若含刚注入的值（或其≥8字符分片）→ 被替换为 token，并记 `redacted_output` 审计。
- [ ] **AC-4**：策略 `default: deny`；未在 `allow_tags` 内 / 命中 `deny_names` / 窗口外命中 `require_approval` 的请求一律拒绝，
  且拒绝原因写入审计。
- [ ] **AC-5**：租约/窗口到期后失效（`lease` 临时件被删、`unlock` 到期后 `require_approval` 重新拒绝）。
- [ ] **AC-6**：每次调用有一条审计；审计不含值；可 `audit --tail` 查看。
- [ ] **AC-7**：caller 身份按 token 校验（常量时间）、未注册降级 `anonymous`、`anonymous` 无法 `run`；token 仅签发时显示一次、可吊销。
- [ ] **AC-8**：`unlock --ttl 600` 窗口为 **10 分钟**；窗口只免 `require_approval`，不放宽 `allow_tags`/`deny_names`/限额。

## Dependencies

- 已有：`paths.py` / `km_config.py`（目录与配置分层）、`map.json`、`sanitize_lib.py`（值指纹 + 替换）。
- 后端：**仅 `map.json`**（ADR-001 D3）。
- 用户本机动作：`enroll` / `unlock` / `lock`（同 Bitwarden 导入的地位）。

## Risk

- **同机隔离边界有限**：Agent 与技能同进程/同用户运行时，Agent 仍可能直接读文件系统。
  真正的隔离必须靠 **OS 层**（不同 OS 用户 + ACL、DPAPI 绑定、或 broker 以独立用户/服务运行）。
  因此本能力定位是「**收口 + 授权 + 审计 + 防误用**」，而非对抗已被完全控制的 Agent。
- **Prompt injection**：injection 可以让 Agent 发起 `run` —— 靠 deny-by-default + 标签白名单 + 审批窗口 + 速率限制降低影响面，
  并用 AC-3 的输出脱敏防「借输出外泄」。
- **审计自身泄漏**：审计只存名字与结果，禁止存命令原文与输出。
- **接口膨胀**：一旦提供 `get`，整套设计失效 —— 必须写进 SKILL.md 红线。
- **caller token 落到仓库**：`enroll` 文档须明示 token 只进 Agent 自己的密钥库/环境变量，不入 Git。

## 设计决策（已定）

| 编号 | 问题 | 决策 | 详见 |
|---|---|---|---|
| D1 | caller 身份 | **两段式**：caller token 定主体（可验证）、通道仅作审计提示；识别链 `--caller-token` → `AGENT_SECRET_TOKEN` → stdin → `anonymous`；未注册只读元数据 | [ADR-001 · D1](../decisions/ADR-001-agent-secret-access.md) |
| D2 | 解锁窗口 | **要，10 分钟（600s）**；用户本机 `unlock`，窗口内免审批但不放宽范围；到期自动失效 | [ADR-001 · D2](../decisions/ADR-001-agent-secret-access.md) |
| D3 | 后端 | **只支持 `map.json`**，保留 `resolve_value()` 抽象位 | [ADR-001 · D3](../decisions/ADR-001-agent-secret-access.md) |
| D4 | MCP 归属 | **放本技能**（`agent_secret_mcp.py`），技能作为统一入口，Agent 无需管理 MCP；未启用 MCP 时 CLI 仍全功能 | [ADR-001 · D4](../decisions/ADR-001-agent-secret-access.md) |

## 里程碑

| 阶段 | 内容 | 风险 |
|---|---|---|
| **P0** ✅（2026-10-22 完成） | `list` + `enroll`（token）+ 策略骨架 + 审计 + 文档红线（**无值出口**） | 低 |
| **P1** ✅（2026-10-22 完成） | `run`（env 注入 + 输出二次脱敏 + 退出码 13/透传）+ `unlock/lock`（600s 窗口）+ `max_calls_per_hour` 限额；AC-2/AC-3/AC-5/AC-8 全绿 | 中：已修 `fnmatch` 把 `[REDACTED:…]` 误当字符类的匹配缺陷 |
| **P2** ✅（2026-10-22 完成） | MCP 薄壳（`secret_list`/`secret_run`/`secret_audit` + `mcp --print-config` + SKILL.md 接入说明） | 中：已与 CLI 共用 `run_secret()` 内核，不分叉 |

## 业界对照（2026）

- **1Password**：Service Account（非人类身份）+ MCP server，值不进上下文；Vault 分层（Dev-AI-Agents / CI-Prod / Personal 永不暴露）。
- **Bitwarden**：Secrets Manager（`bws` + machine account access token，专供 CI/Agent）+ 新开源 **Agent Access（`aac run`）**，
  明确「不建议把凭据直接给 LLM」，推荐 `aac run` 环境注入。
- **HashiCorp Vault**：动态凭据（按需签发、自动过期）；**Agent Vault**（开源 proxy，拦截工具请求注入短时令牌）。
- **社区实践**：TTL 5–15 分钟、tool-call 边界授权、per-agent allowlist、one-shot grant、会话日志 redaction。