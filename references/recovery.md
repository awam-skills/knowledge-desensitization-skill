# 恢复与离线备份

## 1. 「备份 + 镜像」到底能恢复到什么程度？

**能。** 恢复不依赖 Bitwarden 是否可用。真正的「原文真相源」是
`vault/map/map.json`（token → 原值），它在离线备份的**必须件**里。

| 你手里有的东西 | 能恢复出什么 |
|---|---|
| 仅 `sanitized/` 镜像 | 只能看到占位符（`[REDACTED:...]` 或「见保险库：xxx」），**看不到原文** |
| 镜像 + `map.json` | 能还原**所有 token 形态**占位符（凭据 / PII / 熵）→ 等于原文 |
| 镜像（已回填）+ `map.json` + `bw-items.json` | 能还原**引用形态**（「见保险库：xxx」）→ 等于原文 |
| `vault/raw/*.zip.age` + `age-key.txt` | **逐字节**还原整份原始快照（最强的兜底，无需 map.json） |
| 只有 Bitwarden | 能看到凭据值，但**接不回**它在哪篇笔记的哪个位置（map.json 才有 occurrences） |

> 结论：**`map.json` + `salt.bin` 是恢复的核心；Bitwarden 只是方便取用的副本。**
> `bw-items.json` 不含原值（只有 token ↔ 条目名），它只为「引用形态」还原服务。

### 一键校验（推荐每次备份后都跑）

```bash
# 只读校验：镜像 + 备份 能否完整还原（给了 --source 还会与源逐字节比对）
python scripts/recover.py --data-root <root> --verify --source <源目录>

# 期望输出：源比对 一致 N / 不一致 0
```

### 真正重建

```bash
python scripts/recover.py --data-root <root> --apply          # → <root>/recovered/<name>/
python scripts/recover.py --data-root <root> --apply --in-place  # 就地还原镜像（谨慎）
```

`recover.py` 同时处理两种形态，默认写到**独立目录**（不覆盖镜像——镜像是对外分享物，
不该被就地解密）；`--in-place` 才会改镜像，之后该目录即含明文，**勿再分享**。

## 2. 恢复标准流程（新机器 / 灾难恢复）

1. 新建数据根：`python scripts/init_workspace.py --data-root <新根> --source <源> --name notes`
2. 从离线备份取回并放回：`vault/keys/age-key.txt`、`vault/map/salt.bin`、
   `vault/map/map.json`（+ 需要引用形态时再加 `vault/map/bw-items.json`）。
3. 取回脱敏镜像，放到 `<新根>/sanitized/notes/`。

> ⚠️ **跨机陷阱**：`workspace.json` 里记的是**绝对路径**。把数据根整个拷到别处后，
> 它仍指向**原位置**的 mirror/source。恢复前务必二选一：
> ① 重跑 `init_workspace.py --data-root <新根> ...` 重写 `workspace.json`；
> ② 每个脚本都显式传 `--mirror <新根>/sanitized/notes`。
> 否则脚本会去读旧路径下的镜像，可能被"看起来能跑"误导。
4. 校验：`python scripts/recover.py --data-root <新根> --verify`
5. 重建：`python scripts/recover.py --data-root <新根> --apply`
6. 若备份含 `vault/raw/*.zip.age`，可交叉验证：
   `age -d -i vault/keys/age-key.txt vault/raw/<file>.zip.age > notes.zip`
7. 对照备份目录 `MANIFEST.json` 的 sha256 核对文件完整性。

## 3. 分层保存建议（按敏感度分道）

核心原则：**密文与解密密钥分道保存；能公开的与不能公开的分道保存。**

| 层 | 内容 | 敏感度 | 建议保存途径 | 禁止 |
|---|---|---|---|---|
| A | `sanitized/<name>/`（脱敏镜像） | 低（无凭据明文；PII 已占位） | **网盘 / 私有 Git / 对象存储**；也是向量库输入 | — |
| — | `reports/**`（审计报告） | 低，**但含「哪些文件含凭据」的索引** | 归 `meta/` 留存（默认不打包） | **不进 `cloud/`、不上云** |
| B | `vault/raw/*.zip.age`（加密原始快照） | 中（已加密） | **网盘 / 对象存储**（密文，落不可信存储也安全） | 与 A 的密钥放同一处 |
| C | `vault/keys/age-key.txt`、`vault/map/salt.bin`、`vault/map/map.json` | **★高（含明文原值）** | **离线**：密码管理器条目 / 硬件密钥 / 加密 U 盘×2 / 保险柜纸质 | **网盘、Git、任何在线位置** |
| C | `config/literals.json`（字面量明文） | **★高** | 同 C 层（离线） | 同上 |
| D | Bitwarden 保险库本体 | 高（主密码保护） | Bitwarden 官方 + 定期 **加密导出**（`.json` 加口令） | 导出明文放云端 |
| D | `vault/map/bw-items.json` | 低-中（只含条目名，条目名里可能有路径/上下文） | 随 C 层离线备份 | Git |

**`offline_backup.py` 产出的两个目录（对应上表）**

| 产出目录 | 含哪几级 | 一句话 |
|---|---|---|
| `cloud/` | **A + B** = 脱敏镜像 `notes/` + `archive/*.zip.age` | **只有这两类、无其他文件，可直接整目录上传网盘 / 私有 Git** |
| `offline/` | **C + D** = `vault/keys`、`vault/map/{salt,map,bw-items}`、`config/literals.json`、`bitwarden/` | **手动一次性拷到离线介质；禁止上云** |
| `meta/` | 配置 `rules/whitelist/exclude`（+ `--with-reports` 时含 reports） | 非密辅助，**不上云**，与 cloud 分离 |
| 根目录 `MANIFEST.json` / `RESTORE.md` | — | 放根，保证 `cloud/` 保持干净 |

> `reports/**` 默认**不**打入备份：它不含明文，但等于一份「敏感文件名清单」，
> 上云反而扩大暴露面，且对恢复无用。需要审计留存时加 `--with-reports`（落到 `meta/reports/`）。

**为什么要分开**
- B 与 C**必须分道**：加密档（B）可以随处放，因为打开它的钥匙（C）在别处；
  一旦 B 与 C 同处一地，等于明文。
- C 类必须**离线**且有**冗余**（至少 2 份、异地）：它们丢失 = 所有 `[REDACTED]` 永不可逆。
- A 类可以放心上云 / 入 Git —— 这正是整套流程的价值：**公开的能公开，私密的锁起来**。

## 4. 备份节奏建议

| 时机 | 动作 |
|---|---|
| 每次 `sanitize --apply` 后 | `recover.py --verify --source`（确认可逆）+ `offline_backup.py --verify` |
| 每次改了 `rules/literals` 后 | 重新 `offline_backup.py`（含 `--with-literals`，目标盘须安全） |
| 里程碑 / 换机器前 | 加密档（B）上云；三件套（C）更新离线副本并异地一份 |
| 定期（如每季） | **恢复演练**：在临时目录按第 2 节流程走一遍，确认真的能还原 |

## 5. 一句话记住

> **镜像可以公开，钥匙必须离线；密文与钥匙分道；恢复靠 map.json，不靠 Bitwarden。**