---
name: knowledge-desensitization
version: 1.0.0
description: 把本地知识库（Logseq / Obsidian / 为知笔记 / 纯 Markdown）做成「可对外分享的脱敏副本」，并把抽出的凭据沉淀进 Bitwarden、把关键恢复件做离线备份。四层召回（字面量→厂商格式→关键词→熵兜底）识别密钥与 PII，稳定占位符保证幂等，回填把占位符换成人可读的「见保险库：xxx」。目录无关：源目录、镜像目录、数据根都在对话中配置；运行数据（映射表、私钥、明文清单）永不进 Git。在用户提到知识库脱敏、笔记脱敏、导出/分享前清理隐私、扫描并替换 API key、把凭据归档进 Bitwarden、或对脱敏产物做离线备份时使用。
disable-model-invocation: true
---

# knowledge-desensitization（知识库脱敏 → 凭据归档 → 离线备份）

把一份**内部知识库**变成可以放心对外分享的副本：目录结构、双链 `[[page]]`、块引用
`((uuid))`、标签、日期全部保留，只把不该出门的内容换掉；抽出的真凭据进 Bitwarden，
占位符回填成「见保险库：xxx」；最后把**恢复三件套**一次性备份到用户指定的目录。

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
   （`<data_root>/.km/<id>/`：salt / map / 密钥 / literals，**永不外传**）**物理分开**。
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
├── settings.yaml            # 全局默认：data_root / private_backup_dir / tmp_root / defaults.*
├── config/                  # ★全局配置：rules.json / whitelist.json / exclude.json / literals.json
│                            #   作为所有源的默认；新源由 guide.py 自动继承；不进 Git
└── sources/<id>.yaml        # 每个真源一份（可覆盖全局）
```

**每个源的三件套**（`<id>` 由源路径推导，如 `logseq-main`）：

```
<真源>                        # 只读；绝不破坏、不上传
<data_root>/<id>/             # 工作副本：就地脱敏；输入=输出；可上云 / 私有 Git
<data_root>/.km/<id>/         # 秘密材料与运行态：config/ vault/ reports/ workspace.json
                              #   → 永不外传（与工作副本物理分开 = 密文与钥匙分道）
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

# 它按顺序做三件事：
#   1) sync.py      真源 → 工作副本（**增量**：只拷新增/改动；源中删除的文件批量询问）
#   2) sanitize.py  工作副本**就地**脱敏（稳定 token、幂等、可逆；vault/config 落在 .km/）
#   3) materials.py 还原材料落盘到「单独备份目录」（技能执行；建议离线）
# 最后打印**备份建议**（工作副本 → 网盘/私有 Git；材料 → 私密离线）
```

分步等价（`<km>` = `<data_root>/.km/<id>`，`<work>` = `<data_root>/<id>`）：

```bash
python scripts/sync.py      --id <源id> [--apply] [--deleted keep|remove]
python scripts/sanitize.py  --data-root <km> --root <work> --out <work> --in-place --apply
                            [--note-dirs journals,pages] [--all-text]
python scripts/materials.py --id <源id> --apply --verify
```

- **同步增量**：`sync.py` 用 `size+mtime` 快判、必要时回退 `sha256`；源中**删除**的文件
  默认只列出（`on_deleted=ask`），确认后 `keep` 或 `remove`（软移到 `<km>/superseded/`）。
- **就地脱敏**：`sanitize.py --in-place` 直接覆写工作副本；`--note-dirs` / `--all-text`
  控制参与文件（默认 `journals,pages`；非 Logseq 源用 `--all-text`）。
- **可逆**：`map.json` + salt 是原文真相源；`recover.py` 用「工作副本 + `map.json`」重建，
  **不依赖 Bitwarden**。

```bash
# 可逆性校验（对真源逐字节比对，期望「不一致 0」）
python scripts/recover.py --data-root <km> --verify --source <真源>
python scripts/recover.py --data-root <km> --apply        # → recovered/<name>/
```

**技能不代上传**：工作副本与材料的去向由**你**决定（技能只给建议），
避免密钥材料被误传云端。

### 凭据入 Bitwarden（可选，★用户本机）

```bash
# 先在自己终端解锁：$env:BW_SESSION = (bw unlock --raw)
pwsh -File scripts/bitwarden-import.ps1 -DataRoot <km>     # 产出 <km>/vault/map/bw-items.json
python scripts/backfill.py --data-root <km> --apply        # [REDACTED] → 「见保险库：xxx」
```

### 待改进

- **脱敏增量**：当前脱敏是「全量就地、幂等兜底」，暂时不做增量（原因与方向见
  `references/improvements.md`）。

## 误报治理

> 下表 `<root>` 指该源的**秘密材料目录** `<data_root>/.km/<id>`
> （vault / config / reports 所在处；**永不外传**）。

| 场景 | 手段 |
|---|---|
| 某值被误判为凭据，只想「填回原文」 | `python scripts/restore.py --data-root <root> list` → `apply --yes` |
| 某值**以后永远不该命中** | `python scripts/whitelist.py --data-root <root> add "<值>"`（只存指纹） |
| 熵兜底命中的确是真密钥，要入 Bitwarden | `python scripts/promote.py --data-root <root> new|add --fp <8位指纹>` |
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

**恢复靠 `map.json`，不靠 Bitwarden**：`recover.py` 用「工作副本 + map.json」重建原文，
同时处理 `[REDACTED:...]`（token 形态）与「见保险库：xxx」（引用形态）：

```bash
python scripts/recover.py --data-root <root> --verify [--source <源>]   # 只校验
python scripts/recover.py --data-root <root> --apply                    # → <root>/recovered/<name>/
```

> **salt / map 丢失 = 工作副本里的 `[REDACTED]` 永不可逆。** 请把 `--dest` 指向私密离线介质。
> 分层保存与恢复流程见 `references/recovery.md`（密文与密钥分道；副本可上云，钥匙必须离线）。

## 安全红线（务必遵守）

- **绝不打印 / 外传**：`config/literals.json` 的值、`vault/map/{salt.bin,map.json,bw-items.json}`
  的内容、`BW_SESSION`、Bitwarden 主密码。
- 诊断只输出**掩码**（长度 / 熵 / 字符类 / `sha256[:8]`）；不得用临时 `print` 拼接涉密内容。
- **Bitwarden 主密码永不索取、永不接触**；登录 / 解锁 / 导入必须用户本机执行。
- 文件删除只许**软删**（同卷移动归档到 `superseded/`）+ 事后核验，禁止 `rm`/`del`。
- `vault/` 永不入 Git、永不入向量库；带上下文的 `preview-*.html` 只落 `vault/`。

## 参考文档

- `references/pipeline.md` —— 四层召回、占位符、幂等与回填的完整机制
- `references/security.md` —— 威胁模型、护栏清单、事故处置
- `references/recovery.md` —— 恢复流程与分层保存
- `references/commands.md` —— 每个脚本的参数速查
- `references/improvements.md` —— 待改进项（含「脱敏增量」）
- `README.md` —— 安装、快速上手、发布前检查