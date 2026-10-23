# 交接：脱敏工作副本 → Index → RAG / awam

一页跨仓契约。真源只读；**唯一可索引输入**是 `<data_root>/<id>` 脱敏工作副本。

## 1. 数据流

```
真源（只读）
  → 可选整理（logseq-迁移工具 / wiz 清理）
  → 脱敏 pipeline（本仓 scripts/pipeline.py：sync → sanitize → materials → build_index）
  → <data_root>/<id>                 ← 唯一可索引工作副本（含旁路 Index）
  → build_index（pipeline 第 4 步自动；也可用 scripts/build_index.py 单独重跑；--skip-index 可跳过）
  → 旁路 Index：index.json / journal-date-map.json / block-refs.json
  → awam-knowledge（默认 BM25 + 标题×4；hybrid 非默认）
```

## 2. 路径示例（以本机 settings 为准）

| 角色 | 路径 |
|---|---|
| 真源（只读，未脱敏） | `G:\Data\knowledge\logseq\main` |
| 工作副本（可索引） | `G:\Knowledge\data\logseq-main` |
| Index 落点 | 工作副本根下 `index.json` 等（旁路 JSON，默认不改写 .md） |
| 数据根 | `G:\Knowledge\data`（`settings.yaml` → `data_root`） |
| 机密旁路 `.km` | 脱敏仓旁 / `<secrets_root>/<id>/`（vault、map、salt、literals） |
| 私密备份 materials | `I:\Backup\knowledge\...`（`private_backup_dir`） |

## 3. 永不进索引

- `.km/`（vault / map / salt / 私钥）
- `vault/`（若出现在工作副本内）
- `literals.json`、映射与恢复材料
- 私密备份目录下的 materials
- Bitwarden 导出
- **真源**（未脱敏的 `G:\Data\knowledge\...`）
- **禁止**把 Index 写到 `G:\Knowledge\logseq`（那是 logseq2kb 旧设计目标，未与脱敏衔接）

## 4. 交接检查清单

1. sanitize 已对目标源 **apply**（非仅 dry-run），工作副本存在且可读
2. 笔记中可见 `[REDACTED:...]` / vault 占位，抽查无明文密钥
3. **pipeline 自动跑** build_index（第 4 步）；可用 `--skip-index` 跳过；单独重跑仍可用 `python scripts/build_index.py --id <id>`，再交给 awam
4. 工作副本根下可见 `index.json`（及 journal-date-map / block-refs）；正文未被默认改写
5. awam / RAG 的 `--root` **未**指向真源；默认应为 `<data_root>/<id>`
6. awam 默认召回 = **BM25 + 标题×4**；hybrid 仅显式 `--mode hybrid`，评测未证明强于 BM25
7. 索引扫描未纳入 `.km/`、`vault/`、备份盘、Bitwarden 导出
8. 评测脚本若必须碰真源：显式 `--allow-raw-source`（默认关闭）
9. 与 `settings.yaml` + `sources/<id>.yaml` 的 `data_root` / `source` / `id` 一致

## 5. 本地两条命令（示例）

```bash
# 1) Index：pipeline 第 4 步已自动生成；仅需单独重跑时：
python G:/Knowledge/knowledge-desensitization/scripts/build_index.py --id logseq-main

# 2) awam 冒烟（默认 BM25 + 标题×4；自动读 index.json 标题）
node G:/Projects/19AI/skills/awam-knowledge/code/smoke-index.js --root G:/Knowledge/data/logseq-main --queries "知识库|Logseq"
```

## 6. 指向

- 脱敏：本仓 `SKILL.md`、`scripts/pipeline.py`、`scripts/build_index.py`、`references/`
- awam：`G:\Projects\19AI\skills\awam-knowledge\README.md`；默认 ROOT 见 `code/index-root.js`；召回默认见 `code/eval-recall.js` / `smoke-index.js`
