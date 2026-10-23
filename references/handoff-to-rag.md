# 交接：脱敏工作副本 → RAG / awam 索引

一页跨仓契约。真源只读；**唯一可索引输入**是 `<data_root>/<id>` 脱敏工作副本。

## 1. 数据流

```
真源（只读）
  → 可选整理（logseq-迁移工具 / wiz 清理）
  → 脱敏 pipeline（本仓 scripts/pipeline.py）
  → <data_root>/<id>          ← 唯一可索引
  → awam-knowledge / RAG 索引
```

## 2. 路径示例（以本机 settings 为准）

| 角色 | 路径 |
|---|---|
| 真源（只读，未脱敏） | `G:\Data\knowledge\logseq\main` |
| 工作副本（可索引） | `G:\Knowledge\data\logseq-main` |
| 数据根 | `G:\Knowledge\data`（`settings.yaml` → `data_root`） |
| 机密旁路 `.km` | 脱敏仓旁 / `<secrets_root>/<id>/`（vault、map、salt、literals） |
| 私密备份 materials | `I:\Backup\knowledge\...`（`private_backup_dir`） |

## 3. 永不进索引

- `.km/`（vault / map / salt / 私钥）
- `literals.json`、映射与恢复材料
- 私密备份目录下的 materials
- Bitwarden 导出
- **真源**（未脱敏的 `G:\Data\knowledge\...`）

## 4. 交接检查清单

1. sanitize 已对目标源 **apply**（非仅 dry-run），工作副本存在且可读
2. 笔记中可见 `[REDACTED:...]` / vault 占位，抽查无明文密钥
3. awam / RAG 的 `--root` **未**指向真源；默认应为 `<data_root>/<id>`
4. 索引扫描未纳入 `.km/`、`vault/`、备份盘、Bitwarden 导出
5. 评测脚本若必须碰真源：显式 `--allow-raw-source`（默认关闭）
6. 与 `settings.yaml` + `sources/<id>.yaml` 的 `data_root` / `source` / `id` 一致

## 5. 指向

- 脱敏：本仓 `SKILL.md`、`scripts/pipeline.py`、`references/`
- awam：`G:\Projects\19AI\skills\awam-knowledge\README.md`；默认 ROOT 与校验见 `code/index-root.js`
