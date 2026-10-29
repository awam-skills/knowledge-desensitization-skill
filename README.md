# knowledge-desensitization

把本地知识库（Logseq / Obsidian / 为知笔记 / 纯 Markdown）做成**可对外分享的脱敏副本**，
并把抽出的真凭据沉淀进 Bitwarden、把恢复关键件做**离线备份**。

- **真源只读 + 就地脱敏**：真源一个字节不改；脱敏作用在**工作副本**上，输入=输出（同一份）
- **密文与钥匙分道**：工作副本（可上云 / 私有 Git）与秘密材料 `.km/`（永不外传）物理分开
- **两级配置**：全局 `settings.yaml` + 每源 `sources/<id>.yaml`，落在用户配置根，不进 Git
- **增量同步**：技能触发时真源→工作副本**增量**同步；源中删除的文件**批量询问**
- **幂等**：稳定 HMAC 占位符，重复跑不套娃
- **可回填**：`[REDACTED:...]` → 「见保险库：<条目名> `[vault:...]`」，人可读、可追溯且可精确逆映射
- **可离线恢复**：还原材料落「单独备份目录」；技能**只给建议、不代上传**
- **只「用」不「读」密钥**：Agent 取密钥走 broker（`agent_secret.py`）——**命令行为主流程、MCP 可选、不强制**；
  值只注入子进程环境变量，**永不进模型上下文**（有 token 才有身份，未注册 = 只能读元数据）

机制细节见 `SKILL.md` 与 `references/`。
- **→ RAG / awam 交接**：[`references/handoff-to-rag.md`](references/handoff-to-rag.md)（唯一可索引 = `<data_root>/<id>`；pipeline 第 4 步自动跑 `build_index`（可用 `--skip-index` 跳过）；也可单独重跑；再交给 awam；默认 BM25+标题×4）

---

## 1. 安装依赖

| 依赖 | 是否必需 | 安装（Windows / scoop） |
|---|---|---|
| Python 3.10+ | 必需 | 任意发行版 |
| PyYAML | 必需 | 配置读写（`km_config.py`）；`pip install pyyaml` |
| `bw`（Bitwarden CLI） | 可选 | `scoop install bitwarden-cli` |
| `pwsh`（PowerShell 7） | 用 bw 时必需 | `scoop install pwsh` |

> 含中文的 `bitwarden-import.ps1` 必须用 PowerShell 7（`pwsh`）运行；Windows PowerShell 5.1 会因 GBK 报错。

## 2. 快速上手

> **目录约定（默认值都有，可覆盖）**
> - **工作副本** `<data_root>/<id>/`：脱敏产物，可上云 / 入私有 Git。`data_root` **无默认值**，须显式给或由 `guide.py` 首次配置确定。
> - **秘密材料根**：缺省 **`<技能目录>/.km`**（`<秘密材料根>/<id>/` 存 vault/config/reports；含明文钥匙，**永不外传**）。可用 `settings.secrets_root` / 源配置 / `--secrets-root` 覆盖。
> - **临时工作区**：缺省 **`<技能目录>/.tmp`**（用完即弃）。可用 `settings.tmp_root` / `--tmp-root` 覆盖。
> - 后两者都在技能目录下、已被 `.gitignore` 忽略。

```bash
# 2.1 引导一个源（写 <KM_CONFIG_HOME>/sources/<id>.yaml 与 settings.yaml）
#     <KM_CONFIG_HOME> = %USERPROFILE%\.knowledge-manager-skills（可用 env 覆盖）
python scripts/guide.py \
    --source <真源目录> \
    --data-root <数据根> \
    --private-backup-dir <私密备份目录>     # 先加 --dry-run 看将建的结构与配置

# 2.2 一条命令跑完整流程（缺省 dry-run；加 --apply 执行）
python scripts/pipeline.py --id logseq-main --apply
#   1) sync.py        真源 → 工作副本（增量；源中删除的文件批量询问）
#   2) sanitize.py    工作副本就地脱敏（幂等、可逆）
#   3) materials.py   还原材料落盘到「单独备份目录」
#   4) build_index.py 旁路 Index（自动；可用 --skip-index 跳过；默认不改写 .md）
#   → 末尾打印备份建议（工作副本→网盘/私有 Git；材料→私密离线）

# 2.2b 单独重跑 Index（pipeline 已自动跑；仅需补索引时用）
python scripts/build_index.py --id logseq-main
#   产出：<工作副本>/index.json、journal-date-map.json、block-refs.json、index-report.md

# 2.3（可选）明文口令清单：本机复制并填写（绝不外传）
#   cp <秘密材料根>/<id>/config/literals.example.json .../config/literals.json

# 2.4（可选）凭据入 Bitwarden（★你自己终端）
#   $env:BW_SESSION = (bw unlock --raw)
#   pwsh -File scripts/bitwarden-import.ps1                 # 目录自动解析（要的是「秘密材料目录」）
python scripts/backfill.py --data-root <秘密材料根>/<id> --apply   # 回填「见保险库：<条目名> [vault:...]」

# 2.5（可选）可逆性校验（对真源逐字节比对，期望「不一致 0」）
python scripts/recover.py --data-root <秘密材料根>/<id> --verify --source <真源目录>
```

每一步都只打印**计数与路径**，不打印任何凭据值。

## 3. 数据布局（每源三件套）

用户配置根（**不进 Git**）：`%USERPROFILE%\.knowledge-manager-skills`
→ `settings.yaml`（全局参数）+ `config/`（全局规则/白名单/排除/literals）+ `sources/<id>.yaml`（每源，可覆盖全局）

```
<data_root>/
├── <id>/                       # 工作副本（就地脱敏；可上云 / 私有 Git）
│   └── journals/ pages/ ...    # = 脱敏后的知识库内容
└── .km/<id>/                   # ★秘密材料与运行态（永不外传）
    ├── .desensitization-root
    ├── workspace.json          # source / mirror / name
    ├── .gitignore              # 隐私护栏
    ├── config/                 # rules / whitelist / exclude / literals.json*
    ├── vault/                  # ★ keys/ map/ preview/ superseded/
    └── reports/                # 无原文的审计报告
```

独立备份目录（`materials.py` 落盘，**建议离线**）：

```
<private_backup_dir>/<id>/
├── materials/                  # C+D 密材：salt / map / 密钥 / literals
├── meta/                       # 配置（rules / whitelist / exclude）
├── MANIFEST.json
└── RESTORE.md
```

## 4. 隐私边界（重要）

**永不进 Git**：整个秘密材料目录 `.km/<id>/`（含 `vault/`、`config/literals.json`、
`workspace.json`、`reports/`、`bw-*.json`），以及用户配置根下的 `settings.yaml` /
`sources/*.yaml`（不在任何仓库内）。

**可上云 / 私有 Git**：**只有**工作副本 `<data_root>/<id>/`（已脱敏，且其中不含 `.km/`）。

**永不外传**：上述任何文件的内容、`BW_SESSION`、Bitwarden 主密码。

护栏有两道：
1. `.gitignore`（源头不让进索引）
2. `scripts/pre-commit`（提交前扫索引，只报「类型 + 文件:行号」，内容隐藏）

安装钩子：

```bash
cp scripts/pre-commit .git/hooks/pre-commit
chmod +x .git/hooks/pre-commit
```

## 5. 发布前自检

技能仓对外发布前，请逐项确认：

- [ ] `git status` 中没有 `.km/`、`vault/`、`reports/`、`.tmp/`、`config/literals.json`
- [ ] `config/` 里只有 `*.example.json` 与 `rules.json`（无 `literals.json`）
- [ ] 全仓无明文凭据：`git grep -n -E 'AKIA[0-9A-Z]{16}|sk-[A-Za-z0-9_-]{20,}'` 无值命中
- [ ] `references/` / `README.md` / `SKILL.md` 未粘任何真实片段
- [ ] `scripts/` 无 `__pycache__`

## 6. 常见问题

**Q：脚本报「找不到数据根 / 找不到源配置」？**
先跑 `python scripts/guide.py --source <真源目录> --data-root <数据根>`（写入源配置），
或用 `--data-root` / 环境变量 `KM_DATA_ROOT` 显式指定。

**Q：`bw unlock` 在脚本里失败？**
`pwsh -File` 子进程无法弹交互式主密码提示。请在你**自己的交互终端**先
`$env:BW_SESSION = (bw unlock --raw)`，确认 `bw status` 为 `unlocked`，再跑导入脚本（脚本会复用会话）。

**Q：镜像里的 `[REDACTED:...]` 还能还原吗？**
能，只要 `<秘密材料根>/<id>/vault/map/{map.json,salt.bin}` 在。用 `scripts/recover.py`
拿「工作副本 + `map.json`」整体重建，或用 `scripts/restore.py` 按单个 token 填回。
**salt / map 丢了就永远还原不了** —— 所以务必用 `materials.py`
把还原材料落盘到**单独备份目录**并离线多份。

**Q：需要 Bitwarden 才能恢复吗？**
不需要。原文真相源是 `map.json`（含 token→原值）；回填引用内的 `[vault:...]` 主键也直接查 `map.json`。
Bitwarden 只是方便取用的副本，`bw-items.json` 不含原值，也不参与恢复。
分层保存（哪些能上云、哪些必须离线）见 `references/recovery.md`。

**Q：误报太多怎么办？**
`whitelist.py add`（永久跳过）或 `restore.py`（填回原文）；组合口令用 `add_pair_literal.py`。
详见 `SKILL.md` 的「误报治理」。

## 7. 许可

内部技能包。请勿把任何运行数据（`vault/` 等）随本目录一起分发。