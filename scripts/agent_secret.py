# AI生成
"""agent_secret.py — 供 Agent「使用」密钥的凭据代理（broker）P0。

设计决策见 docs/decisions/ADR-001-agent-secret-access.md 与 docs/features/F001-agent-secret-access.md

核心不变量（红线）
- **没有「读值」出口**：本脚本任何子命令都不打印任何条目明文；值只在 P1 的 run（注入子进程 env）出现。
- caller 身份 = **持有 token**（两段式：主体凭 token 校验；通道只作审计标签）。
- **deny-by-default**：未注册=anonymous（只能读 public 标签元数据，不得 run）。
- 审计不含值、不含命令原文。

子命令
    list    列出条目的**元数据**（名/标签/凭据位/敏感级/是否放行）——永不含值
    enroll  用户本机签发/吊销 caller token（token 仅打印一次，落盘只存 sha256）
    whoami  打印当前 caller 的解析结果（principal / 通道 / 依据），不打印 token
    policy  干跑授权判定（--action list|run --name <条目>），打印 allow/deny + 原因
    audit   查看访问审计（--tail N）
    run     ★把值注入子进程 env 执行命令；输出二次脱敏；值绝不回传（P1）
    unlock  ★用户本机：开启解锁窗口（默认 600s=10 分钟，须 --yes）（P1）
    lock    ★用户本机：提前关闭解锁窗口（P1）

待做 P2：MCP 薄壳（secret_list / secret_run / secret_audit）。

用法
    set AGENT_SECRET_TOKEN=agt_...      # Agent 侧只需一个环境变量
    python scripts/agent_secret.py list --id logseq-main
    python scripts/agent_secret.py enroll --id logseq-main --agent claude-code --tags llm-key,secret
    python scripts/agent_secret.py audit --id logseq-main --tail 50

目录解析
    --id <源id>          → <秘密材料根>/<id>（秘密材料根 = --secrets-root 或 settings.secrets_root 或 <技能目录>/.km）
    --data-root <目录>   → 直接当作秘密材料目录（与 sanitize/backfill 等脚本口径一致）

安全
- 本脚本只读取 map.json 的**元数据字段**（rule/label/credential/occurrences），不读取 value；
  value 仅在 _resolve_value() 内部供 P1 使用，且不经任何 CLI 出口。
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import hmac
import json
import os
import re
import secrets
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import km_config  # noqa: E402
import paths  # noqa: E402

# ----------------------------------------------------------------------------
# 常量
# ----------------------------------------------------------------------------

TOKEN_PREFIX = "agt_"
POLICY_NAME = "agent-access.json"
AGENTS_DIRNAME = "agents"
AUDIT_NAME = "secret-access.jsonl"
TOKEN_PREFIX_KEY = "[REDACTED:"

UNLOCK_NAME = "unlock.json"      # D2：本机解锁窗口状态
MAX_UNLOCK_TTL = 3600            # 窗口上限（秒），防止把 10 分钟窗口开成永久
EXIT_DENIED = 13                 # 策略拒绝的退出码（与子进程退出码区分；2 = 用法/内部错误）
DEFAULT_ENV_KEY = "SECRET"       # --as-env 缺省变量名
REDACT_MIN_SHARD = 8             # 注入值「分片兜底」脱敏的最小长度

# 标签 → 敏感级（用于 list/policy 展示与默认拒读判断）
SENSITIVITY = {
    "bank-card": "高(PII)", "id-cn": "高(PII)", "pw": "高", "conn-pw": "高",
    "llm-key": "中", "secret": "中", "secret-hex": "中",
    "github-token": "中", "jwt": "中",
    "entropy": "低",
}
# 永不进 Agent 通道的标签（PII / 非凭据）
HARD_DENY_LABELS = ("bank-card", "id-cn")

DEFAULT_POLICY = {
    "version": 1,
    "default": "deny",
    "public_tags": [],                 # anonymous 只可读这些标签的元数据
    "deny_labels": list(HARD_DENY_LABELS),
    "deny_names": [],
    "unlock_ttl_sec": 600,             # D2：本机解锁窗口 = 10 分钟
    "require_credential": True,        # 只有 credential=true 的条目可被 run
}


def _now() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def _sha256_hex(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _token_hint(tok: str) -> str:
    return "%s…%s" % (TOKEN_PREFIX, tok[-4:]) if tok else ""


def _write_private(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    try:                                # 尽力收紧权限（Windows 上语义有限）
        os.chmod(path, 0o600)
    except OSError:
        pass


# ----------------------------------------------------------------------------
# 路径解析
# ----------------------------------------------------------------------------

def resolve_secret_dir(args) -> str:
    """返回该源的秘密材料目录 <秘密材料根>/<id>（或 --data-root 直指）。"""
    data_root = getattr(args, "data_root", None)
    sid = getattr(args, "id", None)
    sroot = getattr(args, "secrets_root", None)
    if data_root and sid:
        raise SystemExit("--id 与 --data-root 互斥：--data-root 已直接指向秘密材料目录")
    if data_root:
        return os.path.abspath(data_root)
    if not sid:
        raise SystemExit("需要 --id <源id>（或用 --data-root <秘密材料目录>）")
    root = os.path.abspath(sroot) if sroot else km_config.effective_secrets_root()
    return paths.secret_dir(sid, root)


def map_path(root: str) -> str:
    return os.path.join(paths.map_dir(root), "map.json")


def policy_path(root: str) -> str:
    return os.path.join(paths.config_dir(root), POLICY_NAME)


def agent_path(root: str, agent: str) -> str:
    return os.path.join(paths.config_dir(root), AGENTS_DIRNAME, "%s.json" % agent)


def audit_path(root: str) -> str:
    return os.path.join(paths.reports_dir(root), AUDIT_NAME)


# ----------------------------------------------------------------------------
# 数据装载（只读；metadata-only 视图不含 value）
# ----------------------------------------------------------------------------

def load_entries(root: str) -> dict:
    """返回 {name: metadata} —— **元数据视图，刻意不含 value**。"""
    p = map_path(root)
    if not os.path.exists(p):
        raise SystemExit("找不到 %s：请先跑 guide.py + pipeline.py --apply" % p)
    with open(p, encoding="utf-8") as fh:
        data = json.load(fh)
    out = {}
    for name, ent in (data.get("tokens") or {}).items():
        out[name] = {
            "label": ent.get("label") or "?",
            "credential": bool(ent.get("credential")),
            "occurrences": len(ent.get("occurrences") or []),
            "rule": ent.get("rule") or "",
        }
    return out


def _resolve_value(root: str, name: str) -> str:
    """P1 专用：取出条目明文值（仅注入子进程 env，不经任何 CLI 出口）。"""
    p = map_path(root)
    with open(p, encoding="utf-8") as fh:
        data = json.load(fh)
    ent = (data.get("tokens") or {}).get(name)
    if not ent:
        raise SystemExit("条目不存在：%s" % name)
    return ent.get("value") or ""


def load_policy(root: str) -> dict:
    p = policy_path(root)
    pol = dict(DEFAULT_POLICY)
    if os.path.exists(p):
        try:
            with open(p, encoding="utf-8") as fh:
                pol.update(json.load(fh) or {})
        except (OSError, json.JSONDecodeError):
            pass
    return pol


def load_agents(root: str) -> dict:
    d = os.path.join(paths.config_dir(root), AGENTS_DIRNAME)
    out = {}
    if not os.path.isdir(d):
        return out
    for fn in sorted(os.listdir(d)):
        if not fn.endswith(".json"):
            continue
        try:
            with open(os.path.join(d, fn), encoding="utf-8") as fh:
                cfg = json.load(fh) or {}
        except (OSError, json.JSONDecodeError):
            continue
        out[fn[:-5]] = cfg
    return out


# ----------------------------------------------------------------------------
# caller 身份（D1：两段式）
# ----------------------------------------------------------------------------

def resolve_caller(root: str, token: str) -> tuple[str, str, dict | None]:
    """返回 (principal, how, agent_cfg)。token 校验用常量时间比较；未命中 → anonymous。"""
    if not token:
        return "anonymous", "无 token", None
    cand = _sha256_hex(token)
    for agent, cfg in load_agents(root).items():
        stored = str(cfg.get("token_sha256") or "")
        if stored and len(stored) == len(cand) and hmac.compare_digest(stored, cand):
            if cfg.get("revoked_at"):
                return "anonymous", "token 已吊销(%s)" % agent, None
            if cfg.get("enabled") is False:
                return "anonymous", "agent 已停用(%s)" % agent, None
            return agent, "token 匹配", cfg
    return "anonymous", "token 未注册", None


def pick_token(args) -> str:
    if getattr(args, "caller_token", None):
        return args.caller_token
    return os.environ.get("AGENT_SECRET_TOKEN", "")


# ----------------------------------------------------------------------------
# 授权判定（唯一的策略入口；list 与未来的 run 共用）
# ----------------------------------------------------------------------------

def name_match(pat: str, name: str) -> bool:
    """条目名匹配：**只把 `*`/`?` 当通配**，其余字符（含 `[]`）一律按字面。

    不能用 fnmatch：条目名形如 `[REDACTED:llm-key:xxxx]`，fnmatch 会把 `[...]`
    当字符类解析，导致 `--require-approval "[REDACTED:...]"` 这类模式永远匹配不上。
    """
    if not pat:
        return False
    if not any(c in pat for c in "*?"):
        return pat == name
    rx = "".join(".*" if c == "*" else "." if c == "?" else re.escape(c) for c in pat)
    return re.fullmatch(rx, name) is not None


def judge(policy: dict, agent_cfg: dict | None, principal: str, action: str, name: str,
          entry: dict, unlock: dict | None = None) -> tuple[bool, str]:
    label = entry["label"]
    # 全局硬拒（PII/非凭据）在任何角色下都不放行
    if label in (policy.get("deny_labels") or []):
        return False, "命中全局 deny_labels（%s）" % label
    for pat in (policy.get("deny_names") or []):
        if name_match(pat, name):
            return False, "命中全局 deny_names（%s）" % pat

    if principal == "anonymous":
        if action == "run":
            return False, "未注册（anonymous）不得 run；请本机 enroll 后提供 token"
        if label in (policy.get("public_tags") or []):
            return True, "anonymous 可读 public 标签"
        return False, "anonymous 仅可读 public 标签（当前：%s）" % label

    cfg = agent_cfg or {}
    if cfg.get("revoked_at") or cfg.get("enabled") is False:
        return False, "agent 已吊销/停用"
    for pat in (cfg.get("deny_names") or []):
        if name_match(pat, name):
            return False, "命中该 agent 的 deny_names（%s）" % pat
    for lb in (cfg.get("deny_labels") or []):
        if lb == label:
            return False, "命中该 agent 的 deny_labels（%s）" % label
    allow_tags = cfg.get("allow_tags") or []
    if label not in allow_tags:
        return False, "标签 %s 不在该 agent 的 allow_tags" % label

    if action == "run":
        if policy.get("require_credential", True) and not entry["credential"]:
            return False, "非凭据条目（credential=false）不得 run"
        for pat in (cfg.get("require_approval") or []):
            if name_match(pat, name):
                if unlock and scope_allows(unlock.get("scope") or [], name):
                    continue        # 窗口内且在 scope 内：免审批（上方标签/拒读/限额仍生效）
                return False, "该条目需本机 unlock 窗口（agent_secret.py unlock --ttl 600 --yes）"
    return True, "allow"


# ----------------------------------------------------------------------------
# 审计
# ----------------------------------------------------------------------------

def audit_append(root: str, rec: dict) -> None:
    rec = dict(rec)
    rec.setdefault("ts_epoch", int(dt.datetime.now().timestamp()))   # 供限额按时间窗统计
    p = audit_path(root)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")


def audit_tail(root: str, n: int) -> int:
    p = audit_path(root)
    if not os.path.exists(p):
        print("尚无审计记录：%s" % p)
        return 0
    with open(p, encoding="utf-8") as fh:
        lines = fh.read().splitlines()
    print("审计：%s（共 %d 条，显示末 %d 条）" % (p, len(lines), min(n, len(lines))))
    for ln in lines[-n:]:
        try:
            r = json.loads(ln)
        except json.JSONDecodeError:
            continue
        print("  %s  %-10s %-6s %-9s %-34s %s"
              % (r.get("ts", "?"), r.get("principal", "?"), r.get("action", "?"),
                 r.get("result", "?"), r.get("entry", "") or "-",
                 ("[" + r.get("reason", "") + "]") if r.get("reason") else ""))
    return 0


# ----------------------------------------------------------------------------
# 解锁窗口（D2：默认 10 分钟，用户本机开启）
# ----------------------------------------------------------------------------

def state_dir(root: str) -> str:
    return os.path.join(root, "state")


def unlock_path(root: str) -> str:
    return os.path.join(state_dir(root), UNLOCK_NAME)


def read_unlock(root: str) -> dict:
    p = unlock_path(root)
    if not os.path.exists(p):
        return {}
    try:
        with open(p, encoding="utf-8") as fh:
            return json.load(fh) or {}
    except (OSError, json.JSONDecodeError):
        return {}


def window_active(root: str) -> dict | None:
    """窗口生效时返回状态字典（含 scope），否则 None。"""
    st = read_unlock(root)
    exp = st.get("expires_at_epoch")
    if exp and float(exp) > dt.datetime.now().timestamp():
        return st
    return None


def write_unlock(root: str, ttl: int, scope: list, by: str, locked: bool = False) -> dict:
    now = dt.datetime.now().timestamp()
    end = now if locked else now + ttl

    def _iso(ts: float) -> str:
        return dt.datetime.fromtimestamp(ts).astimezone().isoformat(timespec="seconds")

    st = {
        "created_at": _iso(now),
        "expires_at": _iso(end),
        "expires_at_epoch": end,
        "ttl_sec": ttl,
        "scope": scope,
        "by": by,
        "locked_at": _iso(now) if locked else None,
    }
    _write_private(unlock_path(root), json.dumps(st, ensure_ascii=False, indent=2) + "\n")
    return st


def scope_allows(scope: list, name: str) -> bool:
    scope = scope or []
    return "*" in scope or any(name_match(s, name) for s in scope)


# ----------------------------------------------------------------------------
# 限额 与 输出二次脱敏（值只在本进程内驻留，绝不回传模型）
# ----------------------------------------------------------------------------

def count_recent(root: str, principal: str, action: str, seconds: int = 3600) -> int:
    """近 N 秒内该 principal 该动作的**成功**调用数（denied 不计）。"""
    p = audit_path(root)
    if not os.path.exists(p):
        return 0
    cutoff = dt.datetime.now().timestamp() - seconds
    n = 0
    with open(p, encoding="utf-8") as fh:
        for ln in fh:
            try:
                r = json.loads(ln)
            except json.JSONDecodeError:
                continue
            if r.get("principal") != principal or r.get("action") != action:
                continue
            if r.get("result") not in ("ok", "redacted_output"):
                continue
            if float(r.get("ts_epoch") or 0) >= cutoff:
                n += 1
    return n


def load_values(root: str) -> dict:
    """{真值: token} —— 仅供 run 注入与输出脱敏；永不打印。"""
    with open(map_path(root), encoding="utf-8") as fh:
        data = json.load(fh)
    out = {}
    for name, ent in (data.get("tokens") or {}).items():
        v = ent.get("value")
        if v:
            out[v] = name
    return out


def redact_output(text: str, values: dict, injected: str) -> tuple[str, int]:
    """把输出里出现的真值替换为 token；并对**本次注入值**做分片兜底。返回 (文本, 替换次数)。"""
    n = 0
    for v, tok in sorted(values.items(), key=lambda kv: -len(kv[0])):
        if v and v in text:
            n += text.count(v)
            text = text.replace(v, tok)
    if injected and len(injected) >= REDACT_MIN_SHARD:
        for i in range(len(injected) - REDACT_MIN_SHARD + 1):
            shard = injected[i:i + REDACT_MIN_SHARD]
            if shard and shard in text:
                n += text.count(shard)
                text = text.replace(shard, "[REDACTED:shard]")
    return text, n


def cmd_hash(cmd: list) -> str:
    return hashlib.sha256(" ".join(cmd).encode("utf-8")).hexdigest()[:16]


# ----------------------------------------------------------------------------
# 子命令
# ----------------------------------------------------------------------------

def cmd_list(root: str, args) -> int:
    policy = load_policy(root)
    entries = load_entries(root)
    token = pick_token(args)
    principal, how, cfg = resolve_caller(root, token)

    rows, denied = [], 0
    for name in sorted(entries):
        ent = entries[name]
        ok, reason = judge(policy, cfg, principal, "list", name, ent)
        if args.tag and args.tag != ent["label"]:
            continue
        if ok:
            rows.append((name, ent, reason))
        else:
            denied += 1

    if getattr(args, "json", False):
        print(json.dumps({
            "principal": principal, "how": how, "count": len(rows), "denied": denied,
            "entries": [{"name": n, "label": e["label"], "credential": e["credential"],
                         "sensitivity": SENSITIVITY.get(e["label"], "?"),
                         "occurrences": e["occurrences"]} for n, e, _ in rows],
        }, ensure_ascii=False, indent=2))
    else:
        print("caller = %s（%s）| 放行 %d / 拒绝 %d（**只列元数据，永不含值**）"
              % (principal, how, len(rows), denied))
        if not rows:
            print("  （无放行条目：anonymous 需 public 标签；未注册 Agent 请先 enroll）")
        for name, ent, _ in rows:
            print("  %-34s label=%-12s credential=%-5s 敏感=%s 出现=%d"
                  % (name, ent["label"], ent["credential"],
                     SENSITIVITY.get(ent["label"], "?"), ent["occurrences"]))
    audit_append(root, {"ts": _now(), "principal": principal, "channel": "cli",
                        "action": "list", "entry": args.tag or "", "result": "ok",
                        "reason": "", "ttl": 0, "cmd_hash": ""})
    return 0


def cmd_enroll(root: str, args) -> int:
    name = args.agent
    p = agent_path(root, name)
    if args.revoke:
        if not os.path.exists(p):
            raise SystemExit("无此 agent：%s" % p)
        with open(p, encoding="utf-8") as fh:
            cfg = json.load(fh)
        cfg["revoked_at"] = _now()
        _write_private(p, json.dumps(cfg, ensure_ascii=False, indent=2))
        print("已吊销：%s（token 立即失效）" % name)
        return 0

    if not args.tags:
        raise SystemExit("enroll 需要 --tags（逗号分隔，如 llm-key,secret）；吊销用 --revoke")
    if os.path.exists(p) and not args.force:
        raise SystemExit("agent 已存在：%s\n要重签请加 --force（旧 token 立即失效）" % p)

    pol = policy_path(root)
    if not os.path.exists(pol):
        _write_private(pol, json.dumps(DEFAULT_POLICY, ensure_ascii=False, indent=2) + "\n")
        print("已生成策略骨架：%s（default=deny）" % pol)

    token = TOKEN_PREFIX + secrets.token_hex(20)
    cfg = {
        "agent": name,
        "created_at": _now(),
        "enabled": True,
        "token_sha256": _sha256_hex(token),
        "token_hint": _token_hint(token),
        "allow_tags": [t.strip() for t in args.tags.split(",") if t.strip()],
        "deny_labels": [t.strip() for t in (args.deny_labels or "").split(",") if t.strip()],
        "deny_names": [t.strip() for t in (args.deny_names or "").split(",") if t.strip()],
        "require_approval": [t.strip() for t in (args.require_approval or "").split(",") if t.strip()],
        "ttl_sec": args.ttl,
        "max_calls_per_hour": args.max_calls,
        "revoked_at": None,
    }
    _write_private(p, json.dumps(cfg, ensure_ascii=False, indent=2) + "\n")
    print("已签发 caller token（**只显示这一次，不可再取**）：\n")
    print("    %s\n" % token)
    print("资产：%s（只存 sha256 指纹 + 末 4 位提示 %s）" % (p, cfg["token_hint"]))
    print("交给该 Agent（**不要入 Git、不要贴进聊天**）：")
    print("    PowerShell:  setx AGENT_SECRET_TOKEN \"%s\"" % token)
    print("    bash:        export AGENT_SECRET_TOKEN=%s" % token)
    print("授权范围：allow_tags=%s ttl=%ss max_calls/h=%s"
          % (cfg["allow_tags"], args.ttl, args.max_calls))
    return 0


def cmd_whoami(root: str, args) -> int:
    policy = load_policy(root)
    principal, how, cfg = resolve_caller(root, pick_token(args))
    print("principal : %s" % principal)
    print("channel   : cli（仅审计标签，不参与授权）")
    print("依据      : %s" % how)
    if cfg:
        print("allow_tags: %s" % (cfg.get("allow_tags") or []))
        print("ttl/限额  : %ss / %s per hour" % (cfg.get("ttl_sec"), cfg.get("max_calls_per_hour")))
        print("token 提示: %s" % cfg.get("token_hint"))
    else:
        print("anonymous 仅可读 public_tags=%s 的元数据，且不得 run" % (policy.get("public_tags") or []))
    print("策略文件  : %s" % policy_path(root))
    print("红线      : 本脚本无「读值」出口；值只在 P1 run 注入子进程 env。")
    return 0


def cmd_policy(root: str, args) -> int:
    policy = load_policy(root)
    entries = load_entries(root)
    principal, how, cfg = resolve_caller(root, pick_token(args))
    unlock = window_active(root)
    targets = [args.name] if args.name else sorted(entries)
    for name in targets:
        if name not in entries:
            print("  %s → 不存在" % name)
            continue
        ok, reason = judge(policy, cfg, principal, args.action, name, entries[name], unlock)
        print("  %-34s %s %s → %-5s %s" % (name, args.action, principal,
                                           "allow" if ok else "deny", reason))
    return 0


def cmd_audit(root: str, args) -> int:
    return audit_tail(root, args.tail)


# ----------------------------------------------------------------------------
# 入口
# ----------------------------------------------------------------------------

def cmd_run(root: str, args) -> int:
    """★把条目的值注入子进程 env 执行命令；值绝不回传模型（输出二次脱敏 + 审计不含值）。"""
    policy = load_policy(root)
    entries = load_entries(root)
    principal, how, cfg = resolve_caller(root, pick_token(args))
    name = args.name
    cmd = list(args.cmd or [])
    if cmd and cmd[0] == "--":
        cmd = cmd[1:]
    ch = cmd_hash(cmd)
    if name not in entries:
        print("无此条目：%s" % name, file=sys.stderr)
        return 2

    def _deny(reason: str) -> int:
        print("[deny] %s —— %s" % (name, reason), file=sys.stderr)
        audit_append(root, {"ts": _now(), "principal": principal, "channel": "cli",
                            "action": "run", "entry": name, "result": "denied",
                            "reason": reason, "ttl": 0, "cmd_hash": ch})
        return EXIT_DENIED

    unlock = window_active(root)
    ok, reason = judge(policy, cfg, principal, "run", name, entries[name], unlock)
    if not ok:
        return _deny(reason)

    limit = int((cfg or {}).get("max_calls_per_hour") or policy.get("max_calls_per_hour") or 0)
    if limit > 0:
        used = count_recent(root, principal, "run", 3600)
        if used >= limit:
            return _deny("超过 max_calls_per_hour=%d（近 1 小时已成功 %d 次）" % (limit, used))

    if not cmd:
        print("run 需要在 `--` 之后给出命令，例如：\n"
              "  agent_secret.py run --name <条目> --as-env API_KEY -- <命令...>", file=sys.stderr)
        return 2

    value = _resolve_value(root, name)
    key = args.as_env or DEFAULT_ENV_KEY
    env = dict(os.environ)
    env[key] = value
    tmpfile = os.path.abspath(args.as_file) if args.as_file else None
    if tmpfile:
        _write_private(tmpfile, value)
    try:
        proc = subprocess.run(cmd, env=env, capture_output=True)
    except (FileNotFoundError, OSError) as e:
        print("命令无法执行：%s" % e, file=sys.stderr)
        audit_append(root, {"ts": _now(), "principal": principal, "channel": "cli",
                            "action": "run", "entry": name, "result": "error",
                            "reason": type(e).__name__, "ttl": 0, "cmd_hash": ch})
        return 2
    finally:
        if tmpfile:                      # 自建临时件（含密）：先清零再删，避免残留
            try:
                size = os.path.getsize(tmpfile)
                with open(tmpfile, "r+b") as fh:
                    fh.write(b"\0" * size)
                os.remove(tmpfile)
            except OSError:
                pass

    values = load_values(root)
    out, n_out = redact_output(proc.stdout.decode("utf-8", "replace"), values, value)
    err, n_err = redact_output(proc.stderr.decode("utf-8", "replace"), values, value)
    if out:
        sys.stdout.write(out)
    if err:
        sys.stderr.write(err)
    n = n_out + n_err
    audit_append(root, {"ts": _now(), "principal": principal, "channel": "cli",
                        "action": "run", "entry": name,
                        "result": "redacted_output" if n else "ok",
                        "reason": ("输出二次脱敏 %d 处" % n) if n else "",
                        "ttl": int((unlock or {}).get("ttl_sec") or 0), "cmd_hash": ch,
                        "exit": proc.returncode})
    if n:
        print("\n[已拦截] 子进程输出中发现 %d 处密钥真值，已替换为 token（防借输出外泄）。" % n,
              file=sys.stderr)
    return proc.returncode


def cmd_unlock(root: str, args) -> int:
    """★用户本机：开启解锁窗口（默认 600s = 10 分钟）。窗口只免 require_approval，不放宽范围。"""
    if not args.yes:
        print("★ 这是本机人工动作：请显式加 --yes 确认（Agent 不应自行解锁）。", file=sys.stderr)
        return 2
    ttl = max(1, min(int(args.ttl or 600), MAX_UNLOCK_TTL))
    scope = [s.strip() for s in (args.scope or "*").split(",") if s.strip()] or ["*"]
    by = args.by or os.environ.get("USERNAME") or os.environ.get("USER") or "user"
    st = write_unlock(root, ttl, scope, by)
    print("已开启解锁窗口：%s（ttl=%ds，scope=%s，by=%s）" % (st["expires_at"], ttl, scope, by))
    print("窗口内仅免 require_approval；allow_tags / deny_names / 限额 / 审计全部照旧。")
    print("到期自动失效；提前关闭：agent_secret.py lock")
    audit_append(root, {"ts": _now(), "principal": "user:" + by, "channel": "cli",
                        "action": "unlock", "entry": ",".join(scope), "result": "ok",
                        "reason": "ttl=%ds" % ttl, "ttl": ttl, "cmd_hash": ""})
    return 0


def cmd_lock(root: str, args) -> int:
    """★用户本机：提前关闭解锁窗口。"""
    by = os.environ.get("USERNAME") or os.environ.get("USER") or "user"
    st = write_unlock(root, 0, [], by, locked=True)
    print("已关闭解锁窗口（locked_at=%s）；require_approval 条目重新需要 unlock。" % st["locked_at"])
    audit_append(root, {"ts": _now(), "principal": "user:" + by, "channel": "cli",
                        "action": "lock", "entry": "", "result": "ok", "reason": "",
                        "ttl": 0, "cmd_hash": ""})
    return 0


def _add_common(p: argparse.ArgumentParser) -> None:
    """公共参数：顶层与每个子命令都挂一份（SUPPRESS 默认值，避免两种写法互相覆盖）。"""
    p.add_argument("--id", default=argparse.SUPPRESS, help="源 id（→ <秘密材料根>/<id>）")
    p.add_argument("--data-root", default=argparse.SUPPRESS,
                   help="直接指定秘密材料目录（与 sanitize 等脚本口径一致）")
    p.add_argument("--secrets-root", default=argparse.SUPPRESS,
                   help="秘密材料根；缺省 settings.secrets_root 或 <技能目录>/.km")
    p.add_argument("--caller-token", default=argparse.SUPPRESS,
                   help="caller token；缺省读环境变量 AGENT_SECRET_TOKEN")
    p.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                   help="机器可读输出（list）")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="供 Agent「使用」密钥的凭据代理（P0：无值出口）")
    _add_common(ap)
    common = argparse.ArgumentParser(add_help=False)
    _add_common(common)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("list", parents=[common], help="列出条目元数据（永不含值）")
    p.add_argument("--tag", help="只看某标签")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("enroll", parents=[common], help="★用户本机：签发/吊销 caller token")
    p.add_argument("--agent", required=True, help="Agent 名（如 claude-code）")
    p.add_argument("--tags", help="允许的标签，逗号分隔（如 llm-key,secret）")
    p.add_argument("--ttl", type=int, default=600, help="解锁窗口秒数（默认 600=10 分钟）")
    p.add_argument("--max-calls", type=int, default=20, help="每小时调用上限")
    p.add_argument("--deny-labels", help="该 agent 额外拒读标签")
    p.add_argument("--deny-names", help="该 agent 额外拒读条目 glob")
    p.add_argument("--require-approval", help="需本机 unlock 窗口的条目 glob")
    p.add_argument("--revoke", action="store_true", help="吊销该 agent 的 token")
    p.add_argument("--force", action="store_true", help="覆盖已有 agent（旧 token 立即失效）")
    p.set_defaults(func=cmd_enroll)

    p = sub.add_parser("whoami", parents=[common], help="打印当前 caller 解析结果（不打印 token）")
    p.set_defaults(func=cmd_whoami)

    p = sub.add_parser("policy", parents=[common], help="干跑授权判定（无副作用）")
    p.add_argument("--action", choices=["list", "run"], default="run")
    p.add_argument("--name", help="条目名；缺省列全部")
    p.set_defaults(func=cmd_policy)

    p = sub.add_parser("run", parents=[common],
                       help="★把值注入子进程 env 执行命令（值绝不回传）")
    p.add_argument("--name", required=True, help="条目名（[REDACTED:label:hex]）")
    p.add_argument("--as-env", default=DEFAULT_ENV_KEY,
                   help="注入的环境变量名（默认 %s）" % DEFAULT_ENV_KEY)
    p.add_argument("--as-file", help="改为写入该临时文件（600；子进程退出后清零并删除）")
    p.add_argument("cmd", nargs=argparse.REMAINDER, help="`--` 之后要执行的命令")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("unlock", parents=[common], help="★用户本机：开启解锁窗口（默认 600s）")
    p.add_argument("--ttl", type=int, default=600, help="窗口秒数（默认 600=10 分钟；上限 %d）"
                   % MAX_UNLOCK_TTL)
    p.add_argument("--scope", help="放行的 require_approval 条目 glob（逗号分隔；默认 *）")
    p.add_argument("--by", help="操作者（审计用；默认取系统用户名）")
    p.add_argument("--yes", action="store_true", help="确认这是本机人工动作")
    p.set_defaults(func=cmd_unlock)

    p = sub.add_parser("lock", parents=[common], help="★用户本机：提前关闭解锁窗口")
    p.set_defaults(func=cmd_lock)

    p = sub.add_parser("audit", parents=[common], help="查看访问审计")
    p.add_argument("--tail", type=int, default=50)
    p.set_defaults(func=cmd_audit)
    return ap


def main() -> int:
    args = build_parser().parse_args()
    root = resolve_secret_dir(args)
    return args.func(root, args)


if __name__ == "__main__":
    raise SystemExit(main())