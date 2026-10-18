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
2. **隐私数据不进 Git**。运行数据（`vault/`、`sanitized/`、`reports/`、`config/literals.json`、
   `workspace.json`）永远不入库；`.gitignore` + `pre-commit` 双护栏。
3. **目录无关**。技能包放哪儿都行；真实源/镜像/数据根从对话配置，落在 `workspace.json`。
4. **幂等**。占位符是稳定 HMAC token（`[REDACTED:<label>:<hmac8>]`），重复跑不套娃、不漂移。
5. **绝不外传明文**。任何命令都不把凭据值打印到 stdout / 聊天；诊断一律掩码。
6. **Bitwarden 主密码由用户本机输入**。Agent 只跑只读 `bw status` 与 `-DryRun`，绝不代做
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
| Python 3.10+ | 全部脚本（仅标准库） | 必装 |
| `age` / `age-keygen` | 原始快照加密归档 | 可选（不用加密可跳过） |
| `bw`（Bitwarden CLI） | 凭据归档与回填 | 可选（不归档可跳过） |
| `pwsh`（PowerShell 7） | `bitwarden-import.ps1` | 仅在用 Bitwarden 时需要 |

> Windows 上含中文的 `.ps1` 必须用 **PowerShell 7（`pwsh`）**；PowerShell 5.1 会因 GBK 报错。

## 目录无关：先初始化工作区

所有脚本都接受 `--data-root`（也可用环境变量 `DESENS_DATA_ROOT`）。数据根结构：

```
<data-root>/
├── .desensitization-root      # 工作区标记
├── workspace.json             # {"source": <只读源>, "mirror": <镜像>, "name": "notes"}
├── .gitignore                 # 隐私护栏
├── config/                    # rules.json / whitelist.json / exclude.json / literals.json*
├── vault/                     # ★敏感：keys/ map/ raw/ preview/（永不入库）
├── reports/                   # 无原文的审计报告
└── sanitized/<name>/          # 脱敏镜像（向量库唯一输入）
```

```bash
python scripts/init_workspace.py \
    --data-root <data-root> \
    --source   <只读源目录> \
    --name     notes          # 镜像名，默认 notes
# 可选：--mirror <镜像目录>  不填则默认 <data-root>/sanitized/<name>
```

初始化后会提示你在本机自行创建 `config/literals.json`（**明文口令清单，绝不外传**）：
先 `cp config/literals.example.json config/literals.json`，再按示例填写。

## 标准流水线

```bash
# 0. 扫描建档（只读，看命中分布，不写任何东西）
python scripts/scan.py --data-root <root>

# 1. 上下文预览（含原文，只写 vault/，绝不发聊天）；人工判断误报
python scripts/preview.py --data-root <root>

# 2. 脱敏（默认 dry-run；确认后加 --apply 才写镜像）
python scripts/sanitize.py --data-root <root> --apply
#   产出：sanitized/<name>/、vault/map/{map.json,bw-import-<ts>.json}、reports/sanitize-<ts>.{md,json}

# 3. 凭据入 Bitwarden（★用户本机执行；Agent 只可 -DryRun）
#    先在自己终端解锁：$env:BW_SESSION = (bw unlock --raw)
pwsh -File scripts/bitwarden-import.ps1 -DataRoot <root>
#   产出：vault/map/bw-items.json（token → 条目 映射）

# 4. 回填：把 [REDACTED:...] 换成「见保险库：xxx」（默认 dry-run）
python scripts/backfill.py --data-root <root> --apply
#   产出：reports/backfill-<ts>.md

# 5. 原始快照加密归档（可选，需 age）
python scripts/vault.py --data-root <root> keygen     # 首次
python scripts/vault.py --data-root <root> encrypt    # 产出 vault/raw/<name>-<ts>.zip.age

# 6. 分层离线备份（A+B 可上云 / C+D 必须离线）
python scripts/offline_backup.py --data-root <root> --dest <备份目录> --verify

# 7. 校验「备份 + 镜像」可逆（不写盘；给了 --source 会与源逐字节比对）
python scripts/recover.py --data-root <root> --verify --source <源目录>
#   真正重建：python scripts/recover.py --data-root <root> --apply
```

## 误报治理

| 场景 | 手段 |
|---|---|
| 某值被误判为凭据，只想「填回原文」 | `python scripts/restore.py --data-root <root> list` → `apply --yes` |
| 某值**以后永远不该命中** | `python scripts/whitelist.py --data-root <root> add "<值>"`（只存指纹） |
| 熵兜底命中的确是真密钥，要入 Bitwarden | `python scripts/promote.py --data-root <root> new|add --fp <8位指纹>` |
| 组合口令（A/B/C/D 片段拼装） | `python scripts/add_pair_literal.py --data-root <root> ...` |
| 只想看 literals 结构、不泄露明文 | `python scripts/literals_summary.py --data-root <root>`（唯一允许的入口） |

## 离线备份（分层：可上云 / 必须离线）

`offline_backup.py` 按敏感度拆成三个子目录，直接对应网盘与离线两条去路：

| 子目录 | 级别 | 内容 | 去路 |
|---|---|---|---|
| `cloud/` | A+B | 脱敏镜像 `<name>/`、`archive/*.zip.age` | **只有这两类、无其他文件 → 整目录上传网盘/Git** |
| `offline/` | C+D | `vault/keys`、`vault/map/{salt,map,bw-items}`，可选 `config/literals.json`、`bitwarden/` | **手动拷到离线介质** |
| `meta/` | — | 配置 `rules/whitelist/exclude`；可选 reports | 非密，**不上云** |

- `--with-literals`：把 `config/literals.json`（**含明文**）放进 `offline/`（介质须安全）
- `--with-reports`：把 `reports/`（含「敏感文件名索引」）放进 `meta/`；**默认不打包、绝不入 cloud**
- `--bitwarden-export <文件>`：把你在终端生成的 Bitwarden 加密导出放进 `offline/bitwarden/`
- `--verify`：逐文件比对 sha256
- **默认「固定目录 + 增量更新」**：`<dest>/` 即备份根（直接放 `cloud/ offline/ meta/ superseded/ MANIFEST.json RESTORE.md`），重复执行只写「新增/变化」的文件、未变跳过，源里删掉的旧文件**软移** `superseded/<ts>/`
- `--tiers cloud,offline,meta`：只备份指定层。例：只把 **C+D** 备到某介质 → `--dest <介质> --tiers offline --with-literals`
- `--dated`：改回旧的日期快照 `<dest>/knowledge-desensitization-backup-<ts>/`；`--prune-old` 仅在此模式把旧日期目录软移 `superseded/`

**Bitwarden 本体（D 级，非文件）怎么备份**：在你自己的终端
`bw export --format encrypted_json --password "<口令>" --output bw-export.json`
（加密导出，勿用明文 `--format json`），再用 `--bitwarden-export` 并入 `offline/`。

**恢复靠 `map.json`，不靠 Bitwarden**：`recover.py` 用 `备份 + 脱敏镜像` 重建原文，
同时处理 `[REDACTED:...]`（token 形态）与「见保险库：xxx」（引用形态）：

```bash
python scripts/recover.py --data-root <root> --verify [--source <源>]   # 只校验
python scripts/recover.py --data-root <root> --apply                    # → recovered/<name>/
```

> **私钥/salt/map 丢失 = 镜像里的 `[REDACTED]` 永不可逆。** 请把 `--dest` 指向 U 盘或加密网盘。
> 分层保存与恢复流程见 `references/recovery.md`（密文与密钥分道；镜像可上云，钥匙必须离线）。

## 安全红线（务必遵守）

- **绝不打印 / 外传**：`config/literals.json` 的值、`vault/map/{salt.bin,map.json,bw-items.json}`
  的内容、`vault/keys/age-key.txt`、`BW_SESSION`、Bitwarden 主密码。
- 诊断只输出**掩码**（长度 / 熵 / 字符类 / `sha256[:8]`）；不得用临时 `print` 拼接涉密内容。
- **Bitwarden 主密码永不索取、永不接触**；登录 / 解锁 / 导入必须用户本机执行。
- 文件删除只许**软删**（同卷移动归档到 `superseded/`）+ 事后核验，禁止 `rm`/`del`。
- `vault/` 永不入 Git、永不入向量库；带上下文的 `preview-*.html` 只落 `vault/`。

## 参考文档

- `references/pipeline.md` —— 四层召回、占位符、幂等与回填的完整机制
- `references/security.md` —— 威胁模型、护栏清单、事故处置
- `references/commands.md` —— 每个脚本的参数速查
- `README.md` —— 安装、快速上手、发布前检查