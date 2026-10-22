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
| ★敏感 | `.km/**`（含 `vault/**`、`config/literals.json`、`reports/**`） | 只在本地；不进 Git、不进向量库、不发聊天、不进 Dify |
| 可入库 | 工作副本 `<data_root>/<id>/`、`config/*.example.json`、`config/rules.json` | 无明文；可提交 / 可索引 |
| 配置 | `%USERPROFILE%\.knowledge-manager-skills/**`（`settings.yaml` / `sources/*.yaml`） | 不进 Git |
| 技能本体 | `scripts/`、`SKILL.md`、`references/`、`README.md` | 公开；**不得含真实片段** |

## 4. 双护栏

### 4.1 `.gitignore`（源头）

落在**秘密材料目录** `<秘密材料根>/<id>/.gitignore`：拦截 `vault/`、`reports/`、
`workspace.json`、`.desensitization-root`、`config/literals.json{,.bak-*}`、`bw-*.json`。
工作副本（可上云）与之物理分离。

### 4.2 `pre-commit`（提交前）

- `git grep --cached` **单进程**扫索引（逐文件 grep 会起 3000+ 进程，跑满超时）
- 只输出「类型 + 文件:行号」，**内容隐藏**（钩子日志不是新的泄露面）
- 正则与 `config/rules.json` 同源同强度，统一 `(^|[^A-Za-z0-9])` 左边界
- 排除 `vault` / `reports`
- 命中即 `exit 1`；确认误报可 `--no-verify`，并回头补规则

安装：

```bash
cp scripts/pre-commit .git/hooks/pre-commit
chmod +x .git/hooks/pre-commit
```

## 5. 硬红线

- **Bitwarden 主密码永不索取、永不接触**。登录 / 解锁 / 导入必须用户本机执行；
  用户「授权代做」也不突破。Agent 只可跑只读 `bw status` 与 `-DryRun`。
- **绝不打印明文**：`literals.json` 值、`map.json`、`bw-items.json`、
  `BW_SESSION`、Bitwarden 主密码。诊断一律掩码（长度 / 熵 / 字符类 / `sha256[:8]`）。
- **不得用临时 `print` 拼接**涉密内容；查看 literals 结构只走 `literals_summary.py`。
- **删除只许软删**（同卷移动到 `superseded/`）+ 事后核验；禁止 `rm`/`del`/`Remove-Item`。
- 临时测试目录（`.km-*` 等）用完**软删**清理，勿物理删除。

## 6. 事故处置

若发现明文已泄露（例如被 patch 到聊天 / 提交进仓库）：

1. **立刻轮换该凭据**（在源头作废并重发）
2. 若已入 Git：`git filter-repo` 或 BFG 重写历史，并强推；同步清理远端缓存
3. 复盘根因（是护栏缺失，还是人为绕过），补规则 / 补护栏
4. **不要**在后续任何输出里复述泄露值

> 会话密钥 `BW_SESSION` 泄漏的处理：导入流程收尾会 `bw lock` 使该会话失效；仍然建议重新解锁。

## 7. 备份（恢复能力，技能只给建议 + 落材料）

技能**不代上传、不入 Git**。`materials.py` 只把**还原材料**落到 `private_backup_dir`：

- `materials/`（**C+D**）：`vault/map/{salt.bin,map.json,bw-items.json}`、`vault/keys/**`、
  可选 `config/literals.json` —— **拷到私密离线介质；禁止网盘与 Git**
- `meta/`：配置 `rules/whitelist/exclude` —— 非密辅助
- 根 `MANIFEST.json`（sha256）/ `RESTORE.md` —— 完整性与恢复说明
- C+D **暂不做增量**（每次覆盖写，保证与当前 map/salt 一致）
- 逐文件 sha256 校验：`materials.py --apply --verify`

**工作副本**（`<data_root>/<id>/`，无明文）由你自己上传到网盘 / 私有 Git —— 这正是整套
流程的价值：**公开的能公开，私密的锁起来**。两者**必须分道**：一旦同处一地，等于明文。

> `salt.bin`/`map.json` 丢失 = 工作副本里的 `[REDACTED]` 永不可逆；
> `materials/` 请指向 U 盘/密码管理器/保险柜，**禁止网盘与 Git**。