# knowledge-desensitization

把本地知识库（Logseq / Obsidian / 为知笔记 / 纯 Markdown）做成**可对外分享的脱敏副本**，
并把抽出的真凭据沉淀进 Bitwarden、把恢复关键件做**离线备份**。

- **只读源目录**：源一个字节不改，全部写入落在镜像与数据根
- **隐私数据不进 Git**：`.gitignore` + `pre-commit` 双护栏，运行数据永不入库
- **目录无关**：源目录 / 镜像目录 / 数据根都在对话里配置，落在 `workspace.json`
- **幂等**：稳定 HMAC 占位符，重复跑不套娃
- **可回填**：`[REDACTED:...]` → 「见保险库：xxx」，人可读、可追溯
- **可离线恢复**：一条命令把私钥 / salt / 映射表拷到指定目录

机制细节见 `SKILL.md` 与 `references/`。

---

## 1. 安装依赖

| 依赖 | 是否必需 | 安装（Windows / scoop） |
|---|---|---|
| Python 3.10+ | 必需 | 任意发行版；脚本仅用标准库 |
| `age` + `age-keygen` | 可选 | `scoop install age` |
| `bw`（Bitwarden CLI） | 可选 | `scoop install bitwarden-cli` |
| `pwsh`（PowerShell 7） | 用 bw 时必需 | `scoop install pwsh` |

> 含中文的 `bitwarden-import.ps1` 必须用 PowerShell 7（`pwsh`）运行；Windows PowerShell 5.1 会因 GBK 报错。

## 2. 快速上手

```bash
# 2.1 初始化一个数据工作区（源目录只读）
python scripts/init_workspace.py \
    --data-root G:/Knowledge/data \
    --source   G:/Knowledge/my-notes \
    --name     notes

# 2.2 本机创建明文口令清单（绝不外传）
cp G:/Knowledge/data/config/literals.example.json G:/Knowledge/data/config/literals.json
#     然后按示例填写；此文件已在 .gitignore 中，永不入库

# 2.3 扫描 → 预览 → 脱敏
python scripts/scan.py     --data-root G:/Knowledge/data
python scripts/preview.py  --data-root G:/Knowledge/data          # 写 vault/preview/*.html
python scripts/sanitize.py --data-root G:/Knowledge/data --apply  # dry-run 去掉 --apply

# 2.4 凭据入 Bitwarden（★在你自己的终端执行）
#     PowerShell 7:
#       $env:BW_SESSION = (bw unlock --raw)
#       pwsh -File scripts/bitwarden-import.ps1 -DataRoot G:/Knowledge/data

# 2.5 回填「见保险库：xxx」
python scripts/backfill.py --data-root G:/Knowledge/data --apply

# 2.6 离线恢复备份（U 盘 / 加密网盘）
python scripts/offline_backup.py --data-root G:/Knowledge/data --dest E:/kd-backup --verify
```

每一步都会打印**计数与路径**，不打印任何凭据值。

## 3. 数据布局

```
<data-root>/
├── .desensitization-root      # 工作区标记（供脚本自动识别数据根）
├── workspace.json             # source / mirror / name
├── .gitignore                 # 隐私护栏
├── config/                    # rules / whitelist / exclude / literals.json*
├── vault/                     # ★敏感，永不入库
│   ├── keys/age-key.txt
│   ├── map/{salt.bin,map.json,bw-import-*.json,bw-items.json}
│   ├── raw/*.zip.age
│   ├── preview/*.html
│   └── superseded/            # 软删归档（只留最新）
├── reports/                   # 无原文的审计报告
└── sanitized/<name>/          # ★脱敏镜像（向量库唯一输入）
```

## 4. 隐私边界（重要）

**永不进 Git**：`vault/`、`sanitized/`、`reports/`、`workspace.json`、
`.desensitization-root`、`config/literals.json`、`bw-*.json`、`*.age`。

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

- [ ] `git status` 中没有 `vault/`、`sanitized/`、`reports/`、`config/literals.json`
- [ ] `config/` 里只有 `*.example.json` 与 `rules.json`（无 `literals.json`）
- [ ] 全仓无明文凭据：`git grep -n -E 'AKIA[0-9A-Z]{16}|sk-[A-Za-z0-9_-]{20,}'` 无值命中
- [ ] `references/` / `README.md` / `SKILL.md` 未粘任何真实片段
- [ ] `scripts/` 无 `__pycache__`

## 6. 常见问题

**Q：脚本报「找不到数据根」？**
先跑 `init_workspace.py`，或用 `--data-root` / 环境变量 `DESENS_DATA_ROOT` 显式指定。

**Q：`bw unlock` 在脚本里失败？**
`pwsh -File` 子进程无法弹交互式主密码提示。请在你**自己的交互终端**先
`$env:BW_SESSION = (bw unlock --raw)`，确认 `bw status` 为 `unlocked`，再跑导入脚本（脚本会复用会话）。

**Q：镜像里的 `[REDACTED:...]` 还能还原吗？**
能，只要 `vault/map/map.json` + `vault/map/salt.bin` 在。用 `scripts/restore.py` 按 token 填回。
**这三件套 + age 私钥丢了就永远还原不了** —— 所以务必 `offline_backup.py` 做离线备份。

**Q：误报太多怎么办？**
`whitelist.py add`（永久跳过）或 `restore.py`（填回原文）；组合口令用 `add_pair_literal.py`。
详见 `SKILL.md` 的「误报治理」。

## 7. 许可

内部技能包。请勿把任何运行数据（`vault/` 等）随本目录一起分发。