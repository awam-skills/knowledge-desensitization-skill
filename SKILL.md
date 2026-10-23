---
name: knowledge-desensitization
version: 1.0.0
description: 把本地知识库（Logseq / Obsidian / 为知笔记 / 纯 Markdown）做成「可对外分享的脱敏副本」，并把抽出的凭据沉淀进 Bitwarden、把关键恢复件做离线备份。四层召回（字面量→厂商格式→关键词→熵兜底）识别密钥与 PII，稳定占位符保证幂等，回填把占位符换成人可读且可精确逆映射的「见保险库：<条目名> [vault:...]」。目录无关：源目录、镜像目录、数据根都在对话中配置；运行数据（映射表、私钥、明文清单）永不进 Git。在用户提到知识库脱敏、笔记脱敏、导出/分享前清理隐私、扫描并替换 API key、把凭据归档进 Bitwarden、或对脱敏产物做离线备份时使用。另含「供 Agent 使用密钥」的 broker（只暴露「用」不暴露「读」；**命令行为主流程、MCP 可选、不强制**；值永不进模型上下文）。
disable-model-invocation: true
---

# knowledge-desensitization（知识库脱敏 → 凭据归档 → 离线备份）

把一份**内部知识库**变成可以放心对外分享的副本：目录结构、双链 `[[page]]`、块引用
`((uuid))`、标签、日期全部保留，只把不该出门的内容换掉；抽出的真凭据进 Bitwarden，
占位符回填成「见保险库：<条目名> [vault:...]」；最后把**恢复三件套**一次性备份到用户指定的目录。

## 核心不变量

1. **源目录只读**。所有写入只落在镜像目录与数据根，源目录一个字节都不改。
2. **隐私数据不进 Git**。运行数据（`vault/`、`reports/`、`config/literals.json`、
   `workspace.json`）永远不入库；`.gitignore` + `pre-commit` 双护栏。
3. **目录无关 + 两级配置**。技能包放哪儿都行；用户级配置根
   （`%USERPROFILE%\.knowledge-manager-skills`）存全局 `settings.yaml` 与每源
   `sources/<id>.yaml`（每源可覆盖全局）；数据根从对话配置。
4. **真源只读 + 就地脱敏**。真源一个字节都不改；脱敏作用在**工作副本**上且**就地写回**
   （输入与输出是同一份）。
5. **密文与钥匙分道**。工作副本（`<data_root>/<id>/`，可上云/私有 Git）与秘密材料
   （`<秘密材料根>/<id>/`：salt / map / 密钥 / literals，**永不外传**）**物理分开**。
6. **技能不代上传**。工作副本与还原材料的去向由用户决定，技能**只给建议**。
7. **幂等**。占位符是稳定 HMAC token（`[REDACTED:<label>:<hmac8>]`），重复跑不套娃、不漂移。
8. **绝不外传明文**。任何命令都不把凭据值打印到 stdout / 聊天；诊断一律掩码。
9. **Bitwarden 主密码由用户本机输入**。Agent 只跑只读 `bw status` 与 `-DryRun`，绝不代做
   登录 / 解锁 / 导入。

## 何时使用

- 要把 Logseq / Obsidian / 为知笔记 / 纯 Markdown 知识库打包给外部（客户、公开仓库、公众号）
- 笔记里混着 API key、token、口令、手机号、邮箱、身份证/银行卡、内网地址
- 需要「能分享的版本」，但原始笔记不能动、双链不能断
- 要把散落在笔记里的凭据统一收进密码管理器，并保留「哪条对应哪篇笔记」的线索
- 要给脱敏数据做一次**离线恢复备份**（私钥/salt/映射表），防止某天机器坏了无法还原

## 依赖

| 依赖 | 用途 | 缺失时 |
|---|---|---|
| Python 3.10+ | 全部脚本 | 必装 |
| `PyYAML` | 配置读写（`km_config.py`） | 必装（`pip install pyyaml`） |
| `bw`（Bitwarden CLI） | 凭据归档与回填 | 可选（不归档可跳过） |
| `pwsh`（PowerShell 7） | `bitwarden-import.ps1` | 仅在用 Bitwarden 时需要 |

> Windows 上含中文的 `.ps1` 必须用 **PowerShell 7（`pwsh`）**；PowerShell 5.1 会因 GBK 报错。

## 配置与目录（用户级两级配置）

技能输入只有一个**源目录**（"请帮我处理 X 路径"），其余用引导脚本逐步确定。

统一入口 `entry.py`：**没给目标时**——已保存过源则列出并**询问要处理哪一个**，
从未处理过则**要求用户输入**（源目录 + 数据根）；给了 `--source` 走新增，给了 `--id` 走全流程。

**用户配置根**（跨源复用、不进 Git）：`%USERPROFILE%\.knowledge-manager-skills`
（可用环境变量 `KM_CONFIG_HOME` 覆盖）：

```
<KM_CONFIG_HOME>/
├── settings.yaml            # 全局默认：data_root / private_backup_dir / tmp_root（缺省=技能目录下 .tmp）/ defaults.*
├── config/                  # ★全局配置（配置分层的「默认层」）：rules.json / whitelist.json
│                            #   / exclude.json / literals.json；**改完即生效**（不再是建源时的种子快照）；不进 Git
└── sources/<id>.yaml        # 每个真源一份（可覆盖全局）
```

**配置分层（生效顺序，后者覆盖前者）**——这是「改了配置却不生效」的根因所在：

| 层 | 位置 | 角色 |
|---|---|---|
| 1 | `<技能目录>/config/rules.json` | 技能内置模板（基线，随技能升级） |
| 2 | **`<KM_CONFIG_HOME>/config/*.json`** | **你的默认层：改完即生效** |
| 3 | `<秘密材料根>/<id>/config/*.json` | 仅本源覆盖（通常留空/只放差异项） |

- `rules.json` 按 `name`、`literals.json` 按 `id` 合并（**每源覆盖全局**）；`whitelist.json` / `exclude.json` 取**并集**。
- ⚠️ `literal` 模式字面量要求长度 ≥ `min_len_guard`（默认 8），更短的条目**会被静默丢弃**。
  用 `python scripts/literals_summary.py --data-root <km>` 自查：不生效的条目会显式标 `!! 不生效（被丢弃）`。

**每个源的三件套**（`<id>` 由源路径推导，如 `logseq-main`）：

```
<真源>                        # 只读；绝不破坏、不上传
<data_root>/<id>/             # 工作副本：就地脱敏；输入=输出；可上云 / 私有 Git
<秘密材料根>/<id>/         # 秘密材料与运行态：config/ vault/ reports/ workspace.json
                              #   缺省 <技能目录>/.km/<id>；永不外传（与工作副本物理分开 = 密文与钥匙分道）
```

引导一个源（首次配置，写 `sources/<id>.yaml` 与 `settings.yaml`）：

```bash
python scripts/guide.py --source <真源目录> --data-root <数据根> \
    --private-backup-dir <单独备份目录> \
    [--id <源id>] [--note-dirs journals,pages] [--all-text] [--dry-run]
```

> `data_root` **无默认值**，由 `guide.py` 在首次配置时确定；`private_backup_dir` 是
> 「还原材料的**单独备份目录**」（建议离线）。配置模板见 `config/settings.example.yaml`
> 与 `config/source.example.yaml`。

## 标准流水线（技能触发时）

```bash
# 全流程一条命令（缺省 dry-run；--apply 才执行）
python scripts/pipeline.py --id <源id> --apply
# 可用 --skip-index 跳过 Index；单独重跑：python scripts/build_index.py --id <源id>

# 它按顺序做四件事，再给备份建议：
#   1) sync.py        真源 → 工作副本（**增量**：只拷新增/改动；源中删除的文件批量询问）
#   2) sanitize.py    工作副本**就地**脱敏（稳定 token、幂等、可逆；vault/config 落在 .km/）
#   3) materials.py   还原材料落盘到「单独备份目录」（技能执行；建议离线）
#   4) build_index.py 旁路 Index（自动；缺省 dry-run 传 --dry-run；--apply 时真正写；默认不改写 .md）
# 最后打印**备份建议**（工作副本 → 网盘/私有 Git；材料 → 私密离线）
```

分步等价（`<km>` = `<秘密材料根>/<id>`，`<work>` = `<data_root>/<id>`）：

```bash
python scripts/sync.py        --id <源id> [--apply] [--deleted keep|remove]
python scripts/sanitize.py    --data-root <km> --root <work> --out <work> --in-place --apply
                              [--note-dirs journals,pages] [--all-text]
python scripts/materials.py   --id <源id> --apply --verify
python scripts/build_index.py --id <源id>          # pipeline 已自动跑；仅单独重跑时用
```

- **同步增量**：`sync.py` 用 `size+mtime` 快判、必要时回退 `sha256`；源中**删除**的文件
  默认只列出（`on_deleted=ask`），确认后 `keep` 或 `remove`（软移到 `<km>/superseded/`）。
- **就地脱敏**：`sanitize.py --in-place` 直接覆写工作副本；`--note-dirs` / `--all-text`
  控制参与文件（默认 `journals,pages`；非 Logseq 源用 `--all-text`）。
- **可逆**：`map.json` + salt 是原文真相源；`recover.py` 用「工作副本 + `map.json`」恢复，
  **不依赖 Bitwarden**。两种模式：**全量恢复**（整库）与**单文件恢复**（`--file`）。

```bash
# 可逆性校验（对真源逐字节比对，期望「不一致 0」）
python scripts/recover.py --data-root <km> --verify --source <真源>
python scripts/recover.py --data-root <km> --apply        # 全量 → recovered/<name>/
python scripts/recover.py --data-root <km> --file <相对路径> --apply   # 单文件恢复
```

**技能不代上传**：工作副本与材料的去向由**你**决定（技能只给建议），
避免密钥材料被误传云端。

### 凭据入 Bitwarden（可选，★用户本机）

```bash
# 先在自己终端解锁：$env:BW_SESSION = (bw unlock --raw)
pwsh -File scripts/bitwarden-import.ps1                    # 自动解析秘密材料目录（无需传参）
pwsh -File scripts/bitwarden-import.ps1 -Id <源id>          # 或显式 -SecretDir <秘密材料目录>
pwsh -File scripts/bitwarden-import.ps1 -DryRun            # 只预览（不解锁、不导入）
python scripts/backfill.py --data-root <km> --apply        # [REDACTED] → 「见保险库：<条目名> [vault:...]」
```

### 待改进

- **脱敏增量**：当前脱敏是「全量就地、幂等兜底」，暂时不做增量（原因与方向见
  `references/improvements.md`）。
> **→ RAG / awam 交接**：见 [`references/handoff-to-rag.md`](references/handoff-to-rag.md)。数据流：真源→sanitize→**build_index**→awam（默认 BM25+标题×4）。Index 已是 pipeline 第 4 步（自动）；可用 `--skip-index` 跳过；单独重跑仍可用 `python scripts/build_index.py --id <源id>`。

## 误报治理

> 下表 `<root>` 指该源的**秘密材料目录** `<秘密材料根>/<id>`
> （vault / config / reports 所在处；**永不外传**）。

| 场景 | 手段 |
|---|---|
| 某值被误判为凭据，只想「填回原文」 | `python scripts/restore.py --data-root <root> list` → `apply --yes` |
| 某值**以后永远不该命中** | `python scripts/whitelist.py --data-root <root> add "<值>"`（只存指纹） |
| 熵兜底命中的确是真密钥，要入 Bitwarden | `python scripts/promote.py --data-root <root> new|add --fp <8位指纹>` |
| 图片/附件**链接断链**（文件名被 token 化） | 已由**资源路径护栏**根治（脱敏不改资源文件名/目标）；历史损坏用 `recover.py --file <篇> --apply --in-place` 取回原文后重跑 `sanitize.py --apply` |

> **纯 hex 密钥**（`SerpApi Key`、Telegram `api_hash` 等）由 `kw_hex` 规则按**关键词邻域**捕获——L3 熵兜底刻意排除纯 hex，故必须靠关键词补。
> **`App ID` / `api_id` 是公开标识符，刻意保持明文**，不入规则。
| 组合口令（A/B/C/D 片段拼装） | `python scripts/add_pair_literal.py --data-root <root> ...` |
| 只想看 literals 结构、不泄露明文 | `python scripts/literals_summary.py --data-root <root>`（唯一允许的入口） |

## 备份（技能只给建议 + 落材料）

技能**不代上传、不入 Git**：只把「还原必需的材料」落盘到单独备份目录，并给出去路建议。

| 内容 | 位置 | 建议去路 |
|---|---|---|
| 工作副本（已脱敏，对外分享物） | `<data_root>/<id>/` | 网盘 / 私有 Git（由你自行上传） |
| 还原材料 C+D（salt / map / bw-items / literals / 配置） | `<dest>/<id>/materials/` | **强烈建议**放私密离线介质（U 盘×2 / 密码管理器 / 保险柜） |

```bash
# dest 缺省读源配置 / settings.yaml 的 private_backup_dir
python scripts/materials.py --id <id> --apply --verify
```

- 落 `<dest>/<id>/{materials,meta}/` + `MANIFEST.json`（sha256）+ `RESTORE.md`
- C+D **暂不做增量**（每次覆盖写，保证与当前 map/salt 一致）
- **Bitwarden 本体（D 级，非文件）**怎么备份：在你自己的终端
  `bw export --format encrypted_json --password "<口令>" --output bw-export.json`
  （加密导出，勿用明文 `--format json`），自行放入离线介质。

**恢复靠 `map.json`，不靠 Bitwarden**：`recover.py` 用「工作副本 + map.json」恢复原文，
同时处理 `[REDACTED:...]`（token 形态）与「见保险库：<条目名> [vault:...]」（引用形态，
按内嵌主键查 `map.json`），两种模式：

```bash
python scripts/recover.py --data-root <root> --verify [--source <源>]   # 只校验
python scripts/recover.py --data-root <root> --apply                    # 全量 → <root>/recovered/<name>/
python scripts/recover.py --data-root <root> --file <相对路径> --apply  # 单文件恢复
```

> **salt / map 丢失 = 工作副本里的 `[REDACTED]` 永不可逆。** 请把 `--dest` 指向私密离线介质。
> 分层保存与恢复流程见 `references/recovery.md`（密文与密钥分道；副本可上云，钥匙必须离线）。

## 供 Agent 使用密钥（broker：**命令行为主流程，MCP 可选**）

Agent 要的是「**用**密钥」，不是「读密钥」。本技能因此提供 `scripts/agent_secret.py`：
**永不提供读值接口**，只把值注入子进程环境变量；身份 = 持有 caller token。

> **主流程 = 命令行**：`whoami` → `list` → `run`。**不强制、也不要求配置 MCP**；
> MCP 是**可选薄壳**（仅在客户端是 MCP 原生、且不想给 Agent shell 权限时才用）。
> CVO 2026-10-22 定：Agent **不需要「持有值」，能用即可**；优先命令行完成流程。

```bash
# ★用户本机（一次）：签发 token（只显示一次，交给 Agent 存进它自己的密钥库/环境变量）
python scripts/agent_secret.py enroll --id <源id> --agent claude-code --tags llm-key,secret
# Agent 侧：只需一个环境变量（任何框架通用）
#   PowerShell: setx AGENT_SECRET_TOKEN "agt_..."     bash: export AGENT_SECRET_TOKEN=agt_...
python scripts/agent_secret.py whoami --id <源id>          # 我是谁 / 能看什么
python scripts/agent_secret.py list   --id <源id>          # 只列元数据（名/标签/凭据位/敏感级），永不含值
# ★用密钥：把值注入子进程 env（值绝不回传模型；输出二次脱敏；拒绝=13）
python scripts/agent_secret.py run --id <源id> --name "[REDACTED:llm-key:xxxx]" \
    --as-env OPENAI_API_KEY -- <命令...>
# ★用户本机：10 分钟解锁窗口（只免 require_approval）
python scripts/agent_secret.py unlock --id <源id> --ttl 600 --yes   # lock 可提前关
python scripts/agent_secret.py audit  --id <源id> --tail 50
```

- 策略 `<km>/config/agent-access.json`（`default: deny`）+ 每 caller `<km>/config/agents/<agent>.json`（只存 token 的 `sha256`）；
  审计 `<km>/reports/secret-access.jsonl`（不含值）；解锁窗口 `<km>/state/unlock.json`。细节见 `references/agent-access.md`。
- 限额 `max_calls_per_hour` 生效；`run` 的输出会用 `map.json` 全部真值二次脱敏（防借输出外泄）。
- **MCP（P2，可选、非主流程、不强制）**：`scripts/agent_secret_mcp.py`（stdio/JSON-RPC），只暴露
  `secret_list` / `secret_run` / `secret_audit`（**没有 `secret_get`**，值永不出现在响应里）。
  需要时才配，配置由技能生成：`python scripts/agent_secret.py mcp --id <源id>`；**不配也全功能可用**。

## 安全红线（务必遵守）

- **绝不打印 / 外传**：`config/literals.json` 的值、`vault/map/{salt.bin,map.json,bw-items.json}`
  的内容、`BW_SESSION`、Bitwarden 主密码。
- **Agent 取密钥只走 broker，历史也不得新增「读值」接口**：`agent_secret.py` 无 `get`/`show`/`--print`；
  值**永不进模型上下文**（P1 只注入子进程 env）；未注册 Agent = `anonymous` = 只能读元数据、不得 `run`。
- 诊断只输出**掩码**（长度 / 熵 / 字符类 / `sha256[:8]`）；不得用临时 `print` 拼接涉密内容。
- **Bitwarden 主密码永不索取、永不接触**；登录 / 解锁 / 导入必须用户本机执行。
- 文件删除只许**软删**（同卷移动归档到 `superseded/`）+ 事后核验，禁止 `rm`/`del`。
- `vault/` 永不入 Git、永不入向量库；带上下文的 `preview-*.html` 只落 `vault/`。

## 参考文档

- `references/pipeline.md` —— 四层召回、占位符、幂等与回填的完整机制
- `references/security.md` —— 威胁模型、护栏清单、事故处置
- `references/recovery.md` —— 恢复流程与分层保存
- `references/commands.md` —— 每个脚本的参数速查
- `references/agent-access.md` —— Agent 取用密钥（broker）：红线、命令、策略与审计文件格式
- `references/improvements.md` —— 待改进项（含「脱敏增量」）
- `README.md` —— 安装、快速上手、发布前检查