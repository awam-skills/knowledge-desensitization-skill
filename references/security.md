# 安全模型与护栏

## 1. 威胁模型

要防的三件事：

1. **明文凭据随技能仓 / 镜像一起公开**（最严重，历史上真实发生过）
2. **凭据值出现在日志 / 报告 / 聊天 / 命令回显**（二次泄露）
3. **恢复能力丢失**（私钥 / salt / 映射表没备份 → 镜像永不可逆）

## 2. 分类与处置

| 类别 | 例子 | 是否入 Bitwarden |
|---|---|---|
| 真凭据 | API key、token、口令、私钥 | **是**（`credential=true`） |
| PII | 手机号、邮箱、身份证、银行卡、内网地址 | 否（只替换成占位符） |
| 熵兜底 | 无格式高熵串 | 默认否；确认后用 `promote.py` 提升 |

## 3. 数据分级

| 级别 | 路径 | 规则 |
|---|---|---|
| ★敏感 | `vault/**`、`config/literals.json` | 只在本地；不进 Git、不进向量库、不发聊天、不进 Dify |
| 可入库 | `sanitized/<name>/`、`reports/**`、`config/*.example.json`、`config/rules.json` | 无明文；可提交 / 可索引 |
| 技能本体 | `scripts/`、`SKILL.md`、`references/`、`README.md` | 公开；**不得含真实片段** |

## 4. 双护栏

### 4.1 `.gitignore`（源头）

拦截 `vault/`、`sanitized/`、`reports/`、`workspace.json`、`.desensitization-root`、
`config/literals.json{,.bak-*}`、`bw-*.json`、`*.age`。

### 4.2 `pre-commit`（提交前）

- `git grep --cached` **单进程**扫索引（逐文件 grep 会起 3000+ 进程，跑满超时）
- 只输出「类型 + 文件:行号」，**内容隐藏**（钩子日志不是新的泄露面）
- 正则与 `config/rules.json` 同源同强度，统一 `(^|[^A-Za-z0-9])` 左边界
- 排除 `vault` / `sanitized` / `reports`
- 命中即 `exit 1`；确认误报可 `--no-verify`，并回头补规则

安装：

```bash
cp scripts/pre-commit .git/hooks/pre-commit
chmod +x .git/hooks/pre-commit
```

## 5. 硬红线

- **Bitwarden 主密码永不索取、永不接触**。登录 / 解锁 / 导入必须用户本机执行；
  用户「授权代做」也不突破。Agent 只可跑只读 `bw status` 与 `-DryRun`。
- **绝不打印明文**：`literals.json` 值、`map.json`、`bw-items.json`、`age-key.txt`、
  `BW_SESSION`、主密码。诊断一律掩码（长度 / 熵 / 字符类 / `sha256[:8]`）。
- **不得用临时 `print` 拼接**涉密内容；查看 literals 结构只走 `literals_summary.py`。
- **删除只许软删**（同卷移动到 `superseded/`）+ 事后核验；禁止 `rm`/`del`/`Remove-Item`。
- 本技能的临时测试目录（`.kdtest-*`）用完软移清理，勿物理删除。

## 6. 事故处置

若发现明文已泄露（例如被 patch 到聊天 / 提交进仓库）：

1. **立刻轮换该凭据**（在源头作废并重发）
2. 若已入 Git：`git filter-repo` 或 BFG 重写历史，并强推；同步清理远端缓存
3. 复盘根因（是护栏缺失，还是人为绕过），补规则 / 补护栏
4. **不要**在后续任何输出里复述泄露值

> 会话密钥 `BW_SESSION` 泄漏的处理：导入流程收尾会 `bw lock` 使该会话失效；仍然建议重新解锁。

## 7. 备份（恢复能力，按敏感度分层）

`offline_backup.py` 按敏感度拆成三个子目录，对应网盘/离线两条去路：

- `cloud/`（A+B）：脱敏镜像 + `archive/*.zip.age` 加密快照 —— **只有这两类、无其他文件，可整目录上传**
- `offline/`（C+D）：`vault/keys/age-key.txt`、`vault/map/{salt.bin,map.json,bw-items.json}`，
  可选 `--with-literals` 的 `config/literals.json`、`--bitwarden-export` 的 `bitwarden/` —— **手动拷到离线介质**
- `meta/`：配置 `rules/whitelist/exclude`；`--with-reports` 时的 reports —— 非密，**不上云**
- `--verify` 逐文件 sha256 校验；`--prune-old` 旧备份软移

> 三件套（`age-key.txt`/`salt.bin`/`map.json`）丢失 = 镜像里的 `[REDACTED]` 永不可逆；
> `offline/` 请指向 U 盘/密码管理器/保险柜，**禁止网盘与 Git**。
> reports 含「哪些文件含凭据」的索引，故不随 `cloud/` 上云。