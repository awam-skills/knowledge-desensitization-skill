# 脚本参数速查

## 两类 `--data-root`（务必分清）

| 用途 | `--data-root` 指 | 涉及脚本 |
|---|---|---|
| **数据根**（其下有 `<id>/` 工作副本） | 运行产物根 | `entry` / `guide` / `pipeline` / `sync` / `materials` |
| **秘密材料目录** = `<秘密材料根>/<id>` | 存放 `config/ vault/ reports/` | `sanitize` / `scan` / `preview` / `recover` / `restore` / `whitelist` / `promote` / `backfill` / `add_pair_literal` / `literals_summary` / `probe` |

> **秘密材料根**缺省 = **`<技能目录>/.km`**（可被 `settings.secrets_root` / 源配置 `secrets_root` / `--secrets-root` 覆盖；永不入 Git）。
> **临时工作区**缺省 = **`<技能目录>/.tmp`**（可被 `settings.tmp_root` / `--tmp-root` 覆盖）。

所有脚本都接受 `--data-root`；缺省读环境变量 `KM_DATA_ROOT`（兼容旧名 `DESENS_DATA_ROOT`）。
解析失败时报错并提示先跑 `guide.py`。

以下用 `<km>` 表示某源的秘密材料目录 `<秘密材料根>/<id>`，`<work>` 表示工作副本 `<数据根>/<id>`。

---

## entry.py —— 技能统一入口

```bash
python scripts/entry.py                       # 没给目标：列出已保存的源（询问）或要求输入
python scripts/entry.py --list                # 仅列出已保存的源
python scripts/entry.py --source <目录> [--data-root <根>] ...   # → 转发 guide.py（新增源）
python scripts/entry.py --id <源id> [--apply]                    # → 转发 pipeline.py（处理源）
```

## guide.py —— 引导/配置一个源（技能起点）

```bash
python scripts/guide.py --source <真源目录> --data-root <数据根> \
    [--id <源id>] [--name <数据集名>] [--work-dir <工作副本>] \
    [--private-backup-dir <单独备份目录>] [--on-deleted ask|keep|remove] \
    [--note-dirs journals,pages] [--all-text] [--exclude <glob,..>] [--dry-run] [--force]
```

- 写 `<KM_CONFIG_HOME>/sources/<id>.yaml`（`<KM_CONFIG_HOME>` 缺省 `%USERPROFILE%\.knowledge-manager-skills`）
- 建工作副本 `<数据根>/<id>/` 与秘密材料目录 `<秘密材料根>/<id>/`（`config/ vault/{keys,map,raw,preview} reports/`）
- 把技能模板 `rules.json` 与 `whitelist.json` / `exclude.json`（由 `*.example.json` 生成）**种子到全局配置目录** `<KM_CONFIG_HOME>/config/`（缺则建、已存在不覆盖）
- 每源 `<秘密材料根>/<id>/config/` 只放 `literals.example.json`（示例）；**不再做每源快照**，避免快照遮蔽以后对全局配置的修改（改了不生效）
- 写 `workspace.json`、`.desensitization-root`、`.gitignore`（隐私护栏）
- **不创建** `literals.json`：真实字面量写在**全局** `<KM_CONFIG_HOME>/config/literals.json`（所有源共用、**改完即生效**），或写每源 `<秘密材料根>/<id>/config/literals.json`（仅本源覆盖）
- 把 `data_root` / `private_backup_dir` / `tmp_root` 记入 `settings.yaml`
  （`tmp_root` 不指定时即为默认值：**技能目录下的 `.tmp/`**，不入 Git）

## pipeline.py —— 一个源的全流程

```bash
python scripts/pipeline.py --id <源id> [--apply] [--data-root <根>] \
    [--deleted keep|remove] [--with-assets] \
    [--skip-sync] [--skip-sanitize] [--skip-materials] [--skip-index]
```

顺序固定：`sync → sanitize(就地) → materials → build_index → 备份建议`。缺省 dry-run（build_index 亦传 `--dry-run`；`--apply` 时真正写 Index）。

## sync.py —— 真源 → 工作副本（增量）

```bash
python scripts/sync.py --id <源id> [--apply] [--deleted keep|remove] [--json]
python scripts/sync.py --source <真源目录> --data-root <数据根> [--apply]
```

- **增量**：`size+mtime` 快判、必要时回退 `sha256`；只拷新增/改动
- 源中**删除**的文件：默认只列出（`on_deleted=ask`）；`remove` 时**软移**到 `<km>/superseded/<ts>/`
- 清单落 `<km>/sync-manifest.json`；真源只读

## sanitize.py —— 就地脱敏

```bash
python scripts/sanitize.py --data-root <km> --root <work> --out <work> --in-place [--apply] \
    [--note-dirs journals,pages] [--all-text] [--with-assets] [--rules <path>] [--exclude-config <path>]
```

- 缺省 **dry-run**（只打印计数与路径）
- `--in-place`（`--root` 与 `--out` 同路径时自动启用）：直接覆写工作副本
- 产出：`<km>/vault/map/{map.json,bw-import-<ts>.json}`、`<km>/reports/sanitize-<ts>.{md,json}`
- `--note-dirs` / `--all-text` 控制参与文件（默认 `journals,pages`）

## materials.py —— 还原材料落盘（技能执行）

```bash
python scripts/materials.py --id <源id> [--apply] [--verify] [--json] \
    [--dest <单独备份目录>] [--bitwarden-export <文件>]
```

- 落 `<dest>/<id>/`：`materials/`（C+D 密材）、`meta/`（配置）、`MANIFEST.json`、`RESTORE.md`
- `<dest>` 缺省读源配置 / `settings.yaml` 的 `private_backup_dir`
- **C+D 暂不做增量**（每次覆盖写，保证与当前 map/salt 一致）
- 末尾打印**备份建议**（技能只给建议，不代上传/入 Git）

## scan.py —— 只读扫描建档

```bash
python scripts/scan.py --data-root <km>
```

按规则 / 按文件的命中计数（**不含明文**）。不写任何文件。

## preview.py —— 上下文预览（含原文）

```bash
python scripts/preview.py --data-root <km>
```

写 `<km>/vault/preview/preview-<ts>.html`（**含原文，绝不发聊天**），供人工判误报。

## bitwarden-import.ps1 —— 凭据入库（★用户本机）

```powershell
$env:BW_SESSION = (bw unlock --raw)          # 可选：先在你自己的 pwsh 里解锁（勿回显/勿记录）
pwsh -File scripts/bitwarden-import.ps1 [-Id <源id>] [-SecretDir <秘密材料目录>] [-Account <email>] [-DryRun]
```

参数：`-SecretDir`（**秘密材料目录**，含 `vault\map\`；**可省略，脚本自动解析**）、
`-DataRoot`（**兼容别名，同义**）、`-Id`、`-SecretsRoot`、`-Account`、`-BwPath`（默认 `bw`）、
`-File`、`-DryRun`。

> **目录歧义护栏**：本脚本要的是**秘密材料目录** `<秘密材料根>/<id>`（缺省 `<技能目录>/.km/<id>`），
> **不是真源，也不是工作副本**。运行时会打印「真源 / 工作副本 / 秘密材料目录」三者对照 + 解析依据；
> 传错（例如传了工作副本）会被自动跳过并纠正。
> 解析优先级：`-SecretDir` > `-DataRoot`（别名）> `$KM_SECRET_DIR` > `<秘密材料根>/<id>`（`-Id`
> 或 `sources/*.yaml` 唯一源；秘密材料根取 `sources/<id>.yaml` → `settings.yaml` → 默认 `<技能目录>/.km`）
> \> 扫描 `<秘密材料根>/*` 中含 `vault\map\` 的目录。
> 另：`backfill.py` / `agent_secret.py` 的 `--data-root` 同样指**秘密材料目录**（本项目中该参数名二义，勿按「数据根」理解）；
> `agent_secret.py` 另支持 `--id <源id>`（与 `--data-root` 互斥，取其一）。

流程：找最新 `bw-import-*.json` → `bw status` → `-DryRun` 预览 → 复用已解锁会话（否则 `bw unlock --raw`）
→ `bw sync` → `bw import` → `bw list items --search "notes/"` 写 `vault/map/bw-items.json`（>0 才写）
→ `finally { bw lock }`。

## backfill.py —— 回填「见保险库：<条目名> [vault:...]」

```bash
python scripts/backfill.py --data-root <km> [--mirror <dir>] [--apply]
```

- 引用形如 `见保险库：<条目名> [vault:<label>:<hash8>]`；方括号内是唯一主键（`map.json` 的 token 去掉 `REDACTED` 前缀）
- 只替换 `credential=true` 的 label；PII 保留
- 幂等：引用不复用 `[REDACTED:...]`，重复运行不会二次替换
- `--apply` 才写盘 + 生成 `reports/backfill-<ts>.md`
- 只动工作副本，不动真源

## recover.py —— 用「工作副本 + map.json」恢复原文（全量 / 单文件）

```bash
# 全量恢复（整库）
python scripts/recover.py --data-root <km> --verify [--source <真源>]   # 只校验
python scripts/recover.py --data-root <km> --apply                      # → <km>/recovered/<name>/
python scripts/recover.py --data-root <km> --apply --in-place           # 就地还原工作副本（谨慎）

# 单文件恢复（--file 可重复；给目录则展开其下文件）
python scripts/recover.py --data-root <km> --file pages/env-AI.md --verify
python scripts/recover.py --data-root <km> --file pages/env-AI.md --apply [--out <目录>]
```

- **两种模式**：不传 `--file` = 全量恢复；传 `--file` = 只恢复指定文件（复合同一套还原逻辑）
- 同时处理 `[REDACTED:...]`（token 形态）与「见保险库：<条目名> [vault:...]」（引用形态）；
  两者都直接查 `map.json`，**不需要 `bw-items.json`**（它只用于 backfill 建条目名）
- `--verify` 只读；给了 `--source` 会与真源逐字节比对（期望「不一致 0」）
- 输出默认落 `<km>/recovered/<name>/`（`--file` 时为该文件在树中的相对位置），**不覆盖工作副本**
- **恢复不依赖 Bitwarden**：`map.json` 是原文真相源

## restore.py —— 按 token 填回原文

```bash
python scripts/restore.py --data-root <km> list
python scripts/restore.py --data-root <km> apply --token "[REDACTED:...]" --yes
```

`list` 显示占位符形状（掩码）；`apply` 默认 dry-run，须 `--yes`；写审计 `vault/map/restore-log.jsonl`。

## whitelist.py —— 永久跳过误报

```bash
python scripts/whitelist.py --data-root <km> add "<值>"     # 只存 sha256[:8] 指纹
python scripts/whitelist.py --data-root <km> list
```

## promote.py —— 熵兜底提升为 Bitwarden 条目

```bash
python scripts/promote.py --data-root <km> new
python scripts/promote.py --data-root <km> add --fp <8位指纹> [--token "[REDACTED:...]"]
```

输出 `vault/map/bw-promoted-<ts>.json`；只读 `map.json`，不打印明文。

## add_pair_literal.py —— 追加组合口令字面量

```bash
python scripts/add_pair_literal.py --data-root <km> --id <id> \
    --parts "__PART_A__,__PART_B__" [--label pw] [--pad 32]
```

幂等追加到 `config/literals.json`（`require_keyword=false`、`single_mode=anywhere`、
`orders=both`、`single=true`）；写前备份 `.bak-<ts>`；只打印占位符。

## literals_summary.py —— 查看 literals 结构（唯一允许）

```bash
python scripts/literals_summary.py --data-root <km>
```

真实片段只显示 `<len=N>`，占位符原样输出；**不泄露明文**。

本工具还会做**编译检查**：`literal` 模式长度 < `min_len_guard`（默认 8）等
**不会生成规则**的条目会显式标 `!! 不生效（被丢弃）` 并在末尾汇总警告 ——
专门用于排查「改了 `literals.json` 却毫无反应」。

字面量分层：**全局** `<KM_CONFIG_HOME>/config/literals.json`（所有源共用、**改完即生效**）
→ **每源** `<秘密材料根>/<id>/config/literals.json`（按 `id` 合并、覆盖全局）。
输出同时列出「每源现存覆盖项」以便发现陈旧遮蔽。

## probe.py —— 召回侦察（掩码版）

```bash
python scripts/probe.py --data-root <km> [--dirs journals,pages] [--root <真源>]
```

统计「关键词行内候选」与「无关键词高熵候选」，输出 `reports/probe-<ts>.{json,md}`；
只含 文件:行号 + 长度/熵/字符类 + `sha256[:8]` 指纹，**不含原文**。

## agent_secret.py —— 供 Agent「使用」密钥的 broker（P0/P1，无值出口）

```bash
# ★用户本机（一次）：签发 caller token（只显示一次，交给 Agent 存进它自己的密钥库/环境变量）
python scripts/agent_secret.py enroll --id <源id> --agent <名> --tags llm-key,secret \
    [--ttl 600] [--max-calls 20] [--deny-labels a,b] [--deny-names glob,..] \
    [--require-approval glob,..] [--revoke] [--force]

# Agent 侧（--id <源id> 或 --data-root <km>；身份走 --caller-token 或环境变量 AGENT_SECRET_TOKEN）
python scripts/agent_secret.py whoami  --id <源id>
python scripts/agent_secret.py list    --id <源id> [--tag <标签>] [--json]
python scripts/agent_secret.py policy  --id <源id> --action list|run --name "<条目>"
python scripts/agent_secret.py audit   --id <源id> --tail 50

# ★用密钥（P1）：把值注入子进程 env，值绝不回传模型；拒绝=13，子进程退出码透传
python scripts/agent_secret.py run     --id <源id> --name "<条目>" --as-env KEY -- <命令...>
#   [--as-file <路径>]：改写入 600 临时文件（退出后先清零再删）

# ★用户本机（P1）：10 分钟解锁窗口，只免 require_approval；--ttl 上限 3600s
python scripts/agent_secret.py unlock  --id <源id> --ttl 600 --yes [--scope "glob,glob"]
python scripts/agent_secret.py lock    --id <源id>

# ★MCP（P2）：技能生成客户端配置（唯一入口，用户无需自己管 MCP）
python scripts/agent_secret.py mcp     --id <源id> [--print-config]   # 打印可粘贴的 .mcp.json 片段（两写法等价）
python scripts/agent_secret_mcp.py --id <源id>              # stdio MCP 服务本体（由客户端拉起）
```

- **无读值出口**：任何子命令都不打印条目明文（值只在 P1 的 `run` 注入子进程 env）。
- 身份：`--caller-token` > `AGENT_SECRET_TOKEN`；未注册 = `anonymous`（只读 `public_tags` 元数据、不得 run）。
- 落盘：`<km>/config/agent-access.json`（全局策略，`default=deny`）、`<km>/config/agents/<名>.json`
  （只存 token 的 `sha256` + 末 4 位提示）、`<km>/reports/secret-access.jsonl`（审计，不含值）。
- P1（已实现）：`run --as-env KEY -- <cmd>`（注入 env + 输出二次脱敏 + 退出码透传 + `max_calls_per_hour` 限额）、
  `unlock --ttl 600 --yes` / `lock`（本机 10 分钟窗口，落 `<km>/state/unlock.json`）；模式只把 `*`/`?` 当通配。
- P2（已实现，**可选、非主流程**）：MCP 薄壳 `agent_secret_mcp.py`（stdio/JSON-RPC），只暴露 `secret_list` / `secret_run` / `secret_audit`；
  与 CLI 共用 `run_secret()` 内核不分叉；**主流程用上面的命令行，不要求配置 MCP**，需要时才用 `agent_secret.py mcp --id <源id>` 生成配置。
- 详见 `references/agent-access.md`。

## pre-commit —— 提交前护栏

```bash
cp scripts/pre-commit .git/hooks/pre-commit && chmod +x .git/hooks/pre-commit
```

扫暂存索引，命中疑似凭据只报「类型 + 文件:行号」，`exit 1` 拦截。