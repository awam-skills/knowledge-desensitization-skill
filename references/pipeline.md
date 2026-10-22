# 脱敏流水线机制

## 1. 四层召回

`find_hits()` 按**规则顺序**逐层匹配，命中后做「去重叠 + 最长优先 + 位置排序」。
字面量规则永远排在最前（专用优先于通用）。

| 层 | 名称 | 规则来源 | 说明 |
|---|---|---|---|
| L0 | 字面量 | **全局** `<KM_CONFIG_HOME>/config/literals.json` → **每源** `<秘密材料根>/<id>/config/literals.json`（按 `id` 合并，**每源覆盖全局**） | 你亲自填的明文口令 / 片段；`match: literal`（整串）、`context`（关键词邻域）、`pair`（片段组合）。⚠️ `literal` 模式要求长度 ≥ `min_len_guard`（默认 8），更短的条目**不会生效**（`literals_summary.py` 会标 `!! 不生效`） |
| L1 | 厂商格式 | `config/rules.json` | `AKIA…`、`sk-…`、`ghp_…`、`xox…`、JWT、PEM 私钥块（`BEGIN…PRIVATE KEY`）等 |
| L2 | 关键词邻域 | `config/rules.json` | `password = …`、`token: …` 等赋值语境 |
| L3 | 熵兜底 | `config/rules.json`（`mode: entropy`） | 无格式但高熵的随机串：`len≥min_len` 且 `熵≥min_entropy` 且 `字符类≥min_classes`，且不在 `exclude_regex` |

**L3 默认 `credential=false`**：只替换、不入 Bitwarden（避免把版本号 / 文档措辞污染密码库）。

**L2.5 `kw_hex` —— 关键词驱动的十六进制密钥**：`api_hash / API Key / SerpApi / APPID hash / hash`
邻域（可跨 1–2 行）内的 32/64 位纯 hex 判为凭据。补 L3 漏报——L3 的 `exclude_regex`
显式排除纯 hex（`^[0-9a-fA-F]{8,64}$`），导致 API Key 最常见的纯 hex 形态整体漏网。
`App ID / api_id` 属**公开标识符**，刻意**保持明文**（不脱）。

**资源路径护栏（`sanitize_lib.resource_spans`）**：脱敏**不改** markdown 的嵌入名 /
链接目标 / 图片 ALT 里的**资源文件名**（`![[x.png]]`、`](…/assets/x.png)`、`![x.jpg](…)`）。
否则「文件没改名、链接文字被改」→ 图片/附件断链。URL 目标同样保护，
**但**含用户凭据段（形如 `账号:口令@主机`）的 URL 除外。
确认真是密钥时用 `promote.py` 提升。

### pair（组合口令）

语义来自真实使用场景：两个常见片段 A、B，

- 可能**单独存在**（A 或 B 单独出现）
- 可能以 `.*AB.*` 或 `.*BA.*` 形式出现

对应参数：

- `parts: [A, B]`、`orders: both`（AB / BA 都试）
- `single: true`、`single_mode: "anywhere"`（单片段在同行任意位置也命中）
- `require_keyword: false`（无需附近有关键词，裸组合也命中）
- `pad`（片段间允许的填充符，如 `-` `_` 空格）

规则名**不含明文**（`literal:<id>`、`literal:<id>:pN`），避免规则本身成为泄露源。

## 2. 占位符（token）

```
[REDACTED:<label>:<hmac8>]
make_token = hmac_sha256(salt, rule.name + "\0" + value)[:8]
```

- **稳定**：同一 `vault/map/salt.bin` + 同一规则 → 同一 value 永远同一 token → 幂等。
- **不可逆**：只有 salt 才能复算；可逆性来自 `vault/map/map.json`（token → 原值）。
- **保护区**：`TOKEN_RX` 保证已生成的占位符不会被任何规则二次匹配（防套娃）。

## 3. 幂等三要素

1. `TOKEN_RX` 保护区（占位符内部不再匹配）
2. 迭代到**不动点**（`sanitize.py` 最多 4 轮，直到无新命中）
3. 稳定 HMAC token

因此**重复执行 sanitize 是安全的**：工作副本不变，计数不变。
脱敏对工作副本**就地**执行（`--in-place`，幂等覆写），真源始终只读。

## 4. 读写保真

- 一律 `encoding="utf-8", newline=""`（保留原始行尾，避免 CRLF/LF 抖动）
- 保留：文件名（journal 为 `YYYY_MM_DD.md`）、目录树、frontmatter 键、`[[page]]`、`((uuid))`、`#tag`

## 5. 产物与去向

> 下表 `<km>` = 该源的**秘密材料目录** `<秘密材料根>/<id>`（永不外传）。

| 产物 | 位置 | 含原文？ | 入库？ |
|---|---|---|---|
| 脱敏工作副本 | `<data_root>/<id>/`（就地） | 否（仅占位符 / PII / 熵串） | 可选（**仅此份可对外**） |
| 审计报告 | `<km>/reports/sanitize-<ts>.{md,json}` | 否 | 可选 |
| 映射表 | `<km>/vault/map/map.json` | **是** | 永不 |
| BW 导入清单 | `<km>/vault/map/bw-import-<ts>.json` | **是** | 永不 |
| BW 条目标识 | `<km>/vault/map/bw-items.json` | 否（token→条目名） | 永不 |
| 回填报告 | `<km>/reports/backfill-<ts>.md` | 否 | 可选 |
| 上下文预览 | `<km>/vault/preview/preview-<ts>.html` | **是** | 永不 |
| 还原材料 | `<dest>/<id>/{materials,meta}/`（`materials.py`） | **是** | 永不 |

## 6. 回填（backfill）

`backfill.py` 读 `bw-items.json`，取每个条目的自定义字段 `notes-token`，建立

```
token → "见保险库：<条目名去掉 notes/ 前缀> [vault:<label>:<hash8>]"
```

方括号内是唯一主键（`map.json` 的 token 去掉 `REDACTED` 前缀），恢复时直接查 `map.json`
→ 不依赖条目名唯一性；主键不复用 `[REDACTED:...]`，重复回填不会二次替换。

只替换 **`credential=true`** 的 label（PII 如手机号 / 邮箱保留为占位符），
只改工作副本，`--apply` 才写盘并生成报告。

- 只动**工作副本** → 真源始终不变，脱敏结果可随时重算。
- 未映射（token 不在 bw-items 里）会计数提示，便于排查漏导入。

## 7. 误报治理优先级

1. `restore.py`（按 token 把某处填回原文）—— 局部、精确，首选
2. `whitelist.py`（按 `sha256[:8]` 指纹永久跳过）—— 全局、永久

白名单只存指纹，配置文件里**不含任何明文**。