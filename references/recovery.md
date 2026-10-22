# 恢复与备份

## 1. 「工作副本 + map.json」能恢复到什么程度？

**能。** 恢复不依赖 Bitwarden。真正的「原文真相源」是
`<秘密材料根>/<id>/vault/map/map.json`（token → 原值），它在还原材料（C+D）的**必须件**里。

> `<秘密材料根>` 缺省 = **`<技能目录>/.km`**（可被 `settings.secrets_root` / 源配置 / `--secrets-root` 覆盖；永不入 Git）。

| 你手里有的东西 | 能恢复出什么 |
|---|---|
| 仅工作副本 | 只能看到占位符（`[REDACTED:...]` 或「见保险库：<条目名> `[vault:...]`」），**看不到原文** |
| 工作副本 + `map.json` | 能还原**所有占位符**（token 形态 + 引用形态）→ 等于原文 |
| 工作副本（已回填）+ `map.json` | 能还原**引用形态**（按内嵌 `[vault:...]` 主键查 `map.json`）→ 等于原文 |
| 只有 Bitwarden | 能看到凭据值，但**接不回**它在哪篇笔记的哪个位置（map.json 才有 occurrences） |

> 结论：**`map.json`（+ `salt.bin`）是恢复的核心；Bitwarden 只是方便取用的副本。**
> `bw-items.json` 不含原值（只有 token ↔ 条目名），只用于 `backfill` 生成条目名，**不参与恢复**。

### 一键校验（推荐每次脱敏/材料落盘后都跑）

```bash
python scripts/recover.py --data-root <秘密材料根>/<id> --verify --source <真源目录>
# 期望输出：源比对 一致 N / 不一致 0
```

### 真正重建

```bash
python scripts/recover.py --data-root <秘密材料根>/<id> --apply             # → <km>/recovered/<name>/
python scripts/recover.py --data-root <秘密材料根>/<id> --apply --in-place  # 就地还原工作副本（谨慎）
```

`recover.py` 同时处理两种形态，默认写到**独立目录**（不覆盖工作副本——工作副本是对外分享物，
不该被就地解密）；`--in-place` 才会改工作副本，之后该目录即含明文，**勿再分享**。

### 单文件恢复（只取回某篇笔记）

想单独拿回某个文件的原文，不必全量重建：加 `--file`（镜像相对路径，可重复；给目录则展开其下文件）。

```bash
# 只看能不能还原（不写盘）
python scripts/recover.py --data-root <秘密材料根>/<id> --file pages/env-AI.md --verify [--source <真源>]
# 真正写出（默认落 <km>/recovered/<name>/ 下的同名相对位置；也可 --out 指定目录）
python scripts/recover.py --data-root <秘密材料根>/<id> --file pages/env-AI.md --apply --out <目标目录>
```

- 单文件恢复与全量恢复**共用同一套还原逻辑**（token 形态 + 引用形态），校验口径一致。
- 文件不存在时直接报错并列名，退出码 2（不静默跳过）。
- 同样**绝不打印原文**；输出只给计数与路径。

## 2. 恢复标准流程（新机器 / 灾难恢复）

1. 重新配置源（真源路径可能已变）：
   `python scripts/guide.py --source <真源> --data-root <新数据根> --id <id> --name <name>`
2. 从离线备份取回并放回 `<新数据根>/.km/<id>/`：`vault/map/salt.bin`、`vault/map/map.json`。
3. 取回工作副本，放到 `<新数据根>/<id>/`。
4. 校验：`python scripts/recover.py --data-root <新数据根>/.km/<id> --verify`
5. 重建：`python scripts/recover.py --data-root <新数据根>/.km/<id> --apply`
6. 对照 `<private_backup_dir>/<id>/MANIFEST.json` 的 sha256 核对材料完整性。

> ⚠️ **跨机陷阱**：`workspace.json` 与源配置里记的是**绝对路径**。把数据根整个拷到别处后，
> 它仍指向**原位置**的 source / mirror。恢复前务必重跑 `guide.py`（`--force` 重写源配置），
> 否则脚本会去读旧路径下的东西，可能被"看起来能跑"误导。

## 3. 分层保存建议（按敏感度分道）

核心原则：**密文与解密密钥分道保存；能公开的与不能公开的分道保存。**

| 层 | 内容 | 敏感度 | 建议保存途径 | 禁止 |
|---|---|---|---|---|
| A | 工作副本 `<data_root>/<id>/` | 低（无凭据明文；PII 已占位） | **网盘 / 私有 Git / 对象存储**；也是向量库输入 | 与 C 同处一地 |
| — | `<km>/reports/**`（审计报告） | 低，**但含「哪些文件含凭据」的索引** | 留在秘密材料目录（默认不入 A） | **不上云** |
| C | `<km>/vault/map/{salt.bin,map.json}`、`<km>/vault/keys/**`、`<km>/config/literals.json`（字面量明文） | **★高（含明文原值）** | **离线**：密码管理器条目 / 硬件密钥 / 加密 U 盘×2 / 保险柜纸质 | **网盘、Git、任何在线位置** |
| D | Bitwarden 保险库本体 | 高（主密码保护） | Bitwarden 官方 + 定期**加密导出**；随 C 层离线 | 导出明文放云端 |
| D | `<km>/vault/map/bw-items.json` | 低-中（只含条目名，条目名里可能有路径/上下文） | 随 C 层离线备份 | Git |

**`materials.py` 落盘的内容（对应上表，落到 `<private_backup_dir>/<id>/`）**

| 产出 | 含哪几级 | 一句话 |
|---|---|---|
| `materials/` | **C + D（文件部分）** = `vault/map/*`、`vault/keys/*`、`config/literals.json` | **拷到私密离线介质；禁止上云** |
| `meta/` | 配置 `rules/whitelist/exclude` | 非密辅助，与 materials 分离 |
| `MANIFEST.json` / `RESTORE.md` | — | 完整性校验与恢复说明 |

> **技能不代上传**：工作副本上云 / 入 Git 由你自行操作；`materials.py` 只把还原材料落到
> `private_backup_dir`（建议离线），并打印去路建议。C+D **暂不做增量**。

**为什么要分开**
- C 类必须**离线**且有**冗余**（至少 2 份、异地）：丢失 = 工作副本里的 `[REDACTED]` 永不可逆。
- A 类可放心上云 / 入 Git —— 这正是整套流程的价值：**公开的能公开，私密的锁起来**。

## 4. 备份节奏建议

| 时机 | 动作 |
|---|---|
| 每次 `pipeline --apply` 后 | `recover.py --verify --source`（确认可逆）+ `materials.py --apply --verify` |
| 每次改了 `rules/literals` 后 | 重新 `materials.py --apply`（材料含 literals，目标盘须安全） |
| 里程碑 / 换机器前 | 工作副本上云；C 层材料更新离线副本并异地一份 |
| 定期（如每季） | **恢复演练**：在临时目录按第 2 节流程走一遍，确认真的能还原 |

## 5. 一句话记住

> **副本可以公开，钥匙必须离线；密文与钥匙分道；恢复靠 map.json，不靠 Bitwarden。**