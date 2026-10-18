# 脚本参数速查

所有脚本都接受（多数为可选）：

```
--data-root <dir>     数据工作区根；缺省依次取 env DESENS_DATA_ROOT → 脚本上级目录（需带
                      .desensitization-root 或含 vault/）。
```

数据根解析失败时报错并提示先跑 `init_workspace.py`。

---

## init_workspace.py —— 初始化数据工作区

```bash
python scripts/init_workspace.py --data-root <root> --source <只读源> \
    [--mirror <镜像目录>] [--name notes] [--force]
```

- 建 `config/ vault/{keys,map,raw,preview} reports/ sanitized/`
- 从技能 `config/` 复制 `rules.json`，由 `*.example.json` 生成 `whitelist.json` / `exclude.json`（已存在不覆盖）
- 写 `workspace.json`、`.desensitization-root`、`.gitignore`（隐私护栏）
- **不创建** `literals.json`（由你本机填）

## scan.py —— 只读扫描建档

```bash
python scripts/scan.py --data-root <root>
```

输出按规则 / 按文件的命中计数（**不含明文**）。不写任何文件。

## preview.py —— 上下文预览（含原文）

```bash
python scripts/preview.py --data-root <root>
```

写 `vault/preview/preview-<ts>.html`（**含原文，绝不发聊天**），供人工判误报。

## sanitize.py —— 执行脱敏

```bash
python scripts/sanitize.py --data-root <root> [--apply]
```

- 缺省 **dry-run**（只打印计数与路径）
- `--apply`：写镜像 `sanitized/<name>/`，生成 `vault/map/{map.json,bw-import-<ts>.json}`、
  `reports/sanitize-<ts>.{md,json}`
- `--root` / `--out` 可覆盖 `workspace.json` 里的 source / mirror

## bitwarden-import.ps1 —— 凭据入库（★用户本机）

```powershell
$env:BW_SESSION = (bw unlock --raw)          # 在你自己的 pwsh 里先解锁
pwsh -File scripts/bitwarden-import.ps1 -DataRoot <root> [-Account <email>] [-DryRun]
```

参数：`-DataRoot`（必填）、`-Account`、`-BwPath`（默认 `bw`）、`-File`、`-DryRun`。

流程：找最新 `bw-import-*.json` → `bw status` → `-DryRun` 预览 → 复用已解锁会话（否则 `bw unlock --raw`）
→ `bw sync` → `bw import` → `bw list items --search "notes/"` 写 `vault/map/bw-items.json`（>0 才写）
→ `finally { bw lock }`。

## backfill.py —— 回填「见保险库：xxx」

```bash
python scripts/backfill.py --data-root <root> [--mirror <dir>] [--apply]
```

- 只替换 `credential=true` 的 label；PII 保留
- `--apply` 才写盘 + 生成 `reports/backfill-<ts>.md`
- 只动镜像，不动源

## vault.py —— 加密归档

```bash
python scripts/vault.py --data-root <root> keygen    # 生成 age 密钥对
python scripts/vault.py --data-root <root> encrypt   # 源目录 → vault/raw/<name>-<ts>.zip.age
python scripts/vault.py --data-root <root> list
```

## vault_prune.py —— 派生产物只留最新

```bash
python scripts/vault_prune.py --data-root <root> [--apply]
```

对 `vault/raw/*.zip.age`、`vault/map/bw-import-*.json`、`vault/preview/preview-*.html`
各留最新 1 份，其余**软移** `superseded/`。`--apply` 才执行。

## offline_backup.py —— 离线恢复备份

```bash
python scripts/offline_backup.py --data-root <root> --dest <目录> \
    [--include-archives] [--include-literals] [--verify] [--prune-old]
```

- 必须件：`age-key.txt`、`salt.bin`、`map.json`
- 产出 `<dest>/knowledge-desensitization-backup-<ts>/` + `MANIFEST.json` + `RESTORE.md`
- `--verify` 逐文件 sha256；`--prune-old` 旧备份软移

## restore.py —— 按 token 填回原文

```bash
python scripts/restore.py --data-root <root> list
python scripts/restore.py --data-root <root> apply --token "[REDACTED:...]" --yes
```

`list` 显示占位符形状（掩码）；`apply` 默认 dry-run，须 `--yes`；写审计 `vault/map/restore-log.jsonl`。

## whitelist.py —— 永久跳过误报

```bash
python scripts/whitelist.py --data-root <root> add "<值>"     # 只存 sha256[:8] 指纹
python scripts/whitelist.py --data-root <root> list
```

## promote.py —— 熵兜底提升为 Bitwarden 条目

```bash
python scripts/promote.py --data-root <root> new
python scripts/promote.py --data-root <root> add --fp <8位指纹> [--token "[REDACTED:...]"]
```

输出 `vault/map/bw-promoted-<ts>.json`；只读 `map.json`，不打印明文。

## add_pair_literal.py —— 追加组合口令规则

```bash
python scripts/add_pair_literal.py --data-root <root> --id <id> \
    --part-a "<值>" --part-b "<值>" [--label "<标签>"]
```

幂等追加到 `config/literals.json`（`require_keyword=false`、`single_mode=anywhere`、
`orders=both`、`single=true`）；写前备份 `.bak-<ts>`；只打印占位符。

## literals_summary.py —— 查看 literals 结构（唯一允许）

```bash
python scripts/literals_summary.py --data-root <root>
```

真实片段只显示 `<len=N>`，占位符原样输出；**不泄露明文**。

## pre-commit —— 提交前护栏

```bash
cp scripts/pre-commit .git/hooks/pre-commit && chmod +x .git/hooks/pre-commit
```

扫暂存索引，命中疑似凭据只报「类型 + 文件:行号」，`exit 1` 拦截。