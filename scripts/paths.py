# AI生成
"""目录无关解析：把「数据工作区根」与「真实源/镜像目录」从脚本位置里解耦。

设计目标（用户 2026-10-18 需求）
- 本技能包可以放在任意位置（例如 G:\\Knowledge\\knowledge-desensitization），
  它**不假设**自己旁边就是数据；所有的真实路径都从对话里配置。
- 一次性的目录配置落在数据根下的 workspace.json：
      { "source": "<只读源目录>", "mirror": "<脱敏镜像目录>", "name": "notes" }
  source/mirror 均可被命令行参数覆盖，做到「目录无关」。

数据根（data-root）解析优先级
  1) 命令行 --data-root <dir>
  2) 环境变量 DESENS_DATA_ROOT
  3) 脚本上级目录（当本文件位于 <data-root>/scripts/ 时）
     仅当该目录带工作区标记 .desensitization-root 或含 vault/ 子目录时才认。
  否则报错，并提示先运行 init_workspace.py。

安全
- 本模块只做路径计算与读取 workspace.json，绝不读取或打印任何凭据内容。
"""
from __future__ import annotations

import json
import os

MARKER = ".desensitization-root"

# Bitwarden 条目约定（统一命名，避免各脚本写死）：
#   name   形如 "<ITEM_PREFIX>/<来源> · <摘要>"
#   fields 里以 TOKEN_FIELD 记录 token，供 backfill 建立 token→条目 映射
ITEM_PREFIX = "notes"
TOKEN_FIELD = "notes-token"
VAULT_REF = "见保险库："   # 回填时写进镜像的引用前缀


def is_data_root(path: str) -> bool:
    """判断某目录是否像「已初始化的数据工作区」。"""
    if not path or not os.path.isdir(path):
        return False
    return (os.path.exists(os.path.join(path, MARKER))
            or os.path.isdir(os.path.join(path, "vault")))


def resolve_data_root(explicit: str | None = None) -> str:
    if explicit:
        return os.path.abspath(explicit)
    env = os.environ.get("DESENS_DATA_ROOT")
    if env:
        return os.path.abspath(env)
    # 兼容旧布局：脚本位于 <data-root>/scripts/
    cand = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if is_data_root(cand):
        return cand
    raise SystemExit(
        "未指定数据根（data-root）。三选一：\n"
        "  1) 命令加 --data-root <目录>\n"
        "  2) 设置环境变量 DESENS_DATA_ROOT=<目录>\n"
        "  3) 先执行：python scripts/init_workspace.py --data-root <目录> --source <源目录>\n"
        "（数据根是存放 config/ vault/ sanitized/ reports/ 的工作区，不是本技能包目录）")


def add_root_arg(parser) -> None:
    """给子命令加统一的 --data-root 参数。"""
    parser.add_argument(
        "--data-root", metavar="DIR",
        help="数据工作区根；缺省读 $DESENS_DATA_ROOT，或脚本上级目录（含工作区标记时）")


# ---- 标准子目录 ----

def config_dir(root: str) -> str:
    return os.path.join(root, "config")


def vault_dir(root: str) -> str:
    return os.path.join(root, "vault")


def map_dir(root: str) -> str:
    return os.path.join(root, "vault", "map")


def reports_dir(root: str) -> str:
    return os.path.join(root, "reports")


def sanitized_dir(root: str) -> str:
    return os.path.join(root, "sanitized")


# ---- workspace.json ----

def workspace_path(root: str) -> str:
    return os.path.join(root, "workspace.json")


def load_workspace(root: str) -> dict:
    p = workspace_path(root)
    if not os.path.exists(p):
        return {}
    try:
        with open(p, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {}


def default_source(root: str) -> str | None:
    return load_workspace(root).get("source")


def default_mirror(root: str) -> str | None:
    ws = load_workspace(root)
    m = ws.get("mirror")
    if m:
        return m
    name = ws.get("name") or "notes"
    return os.path.join(root, "sanitized", name)