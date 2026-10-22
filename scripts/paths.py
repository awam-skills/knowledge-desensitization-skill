# AI生成
"""paths.py — 目录无关解析：用户配置根 / 数据根 / 工作副本 / 秘密材料目录。

新架构（用户 2026-10-22 定稿）
- 用户级配置根（跨源复用，不进 Git）：
      KM_CONFIG_HOME（env）或 %USERPROFILE%\\.knowledge-manager-skills
      ├─ settings.yaml           全局默认
      └─ sources/<id>.yaml       每个真源一份（可覆盖全局）
- 数据根（data_root）：运行产物根，可配；其下按「源」分目录：
      <data_root>/<id>/          工作副本（就地脱敏；建议上传网盘 / 私有 Git）
      <data_root>/.km/<id>/      秘密材料与运行态（vault/ config/ state；永不外传）

一个「源」= 一个只读真源 + 一份工作副本 + 一份秘密材料目录：
- 真源：只读，不破坏、不上传。
- 工作副本：脱敏「就地」对象；输入与输出（已脱敏）是同一份。
- 秘密材料：salt / map / age 私钥 / literals 等，落在 <data_root>/.km/<id>/，
  与工作副本物理分开，确保「密文与钥匙分道」——工作副本可上云，秘密材料绝不可。

数据根解析优先级（**无默认值**）
  1) 命令行 --data-root <dir>
  2) 环境变量 KM_DATA_ROOT（旧变量 DESENS_DATA_ROOT 亦兼容）
  3) 用户级 settings.yaml 的 data_root（由 guide.py 首次配置时写入）

工作副本（mirror）解析
  **没有默认**：只认秘密材料目录里 workspace.json 的 mirror；缺失即报错，
  调用方应显式传 --mirror / --out，或先跑 guide.py 建立源配置。

安全
- 本模块只做路径计算与读取配置，绝不读取或打印任何凭据内容。
"""
from __future__ import annotations

import json
import os

MARKER = ".desensitization-root"

# 秘密材料相对数据根的隐藏目录（工作副本的同级兄弟，绝不随工作副本上传）
SECRETS_DIRNAME = ".km"

# 临时工作区：处理「其他数据」与脱敏过程临时文件（用完即弃）。
# **默认落在技能目录下**（<技能目录>/.tmp），永不入 Git；可被用户配置覆盖。
TMP_DIRNAME = ".tmp"


def skill_root() -> str:
    """本技能包根目录（scripts/ 的上级）。"""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def default_tmp_root() -> str:
    """临时工作区的默认根：<技能目录>/.tmp。"""
    return os.path.join(skill_root(), TMP_DIRNAME)

# Bitwarden 条目约定（统一命名，避免各脚本写死）：
#   name   形如 "<ITEM_PREFIX>/<来源> · <摘要>"
#   fields 里以 TOKEN_FIELD 记录 token，供 backfill 建立 token→条目 映射
ITEM_PREFIX = "notes"
TOKEN_FIELD = "notes-token"
VAULT_REF = "见保险库："   # 回填时写进镜像的引用前缀


# ----------------------------------------------------------------------------
# 用户配置根
# ----------------------------------------------------------------------------

def km_config_home() -> str:
    """用户级配置根：<KM_CONFIG_HOME> 或 %USERPROFILE%\\.knowledge-manager-skills。"""
    env = os.environ.get("KM_CONFIG_HOME")
    if env:
        return os.path.abspath(env)
    home = os.environ.get("USERPROFILE") or os.path.expanduser("~")
    return os.path.join(home, ".knowledge-manager-skills")


def settings_path() -> str:
    return os.path.join(km_config_home(), "settings.yaml")


def sources_dir() -> str:
    return os.path.join(km_config_home(), "sources")


def source_cfg_path(sid: str) -> str:
    return os.path.join(sources_dir(), f"{sid}.yaml")


# ----------------------------------------------------------------------------
# 数据根解析
# ----------------------------------------------------------------------------

def resolve_data_root(explicit: str | None = None) -> str:
    if explicit:
        return os.path.abspath(explicit)
    for env_name in ("KM_DATA_ROOT", "DESENS_DATA_ROOT"):
        env = os.environ.get(env_name)
        if env:
            return os.path.abspath(env)
    # 新架构：数据根来自用户级 settings.yaml（无默认值）
    import km_config
    root = km_config.load_settings().get("data_root")
    if root:
        return os.path.abspath(root)
    raise SystemExit(
        "未指定数据根（data_root）。三选一：\n"
        "  1) 命令加 --data-root <目录>\n"
        "  2) 设置环境变量 KM_DATA_ROOT=<目录>\n"
        "  3) 先执行：python scripts/guide.py --source <真源目录>\n"
        "（数据根是存放工作副本与秘密材料的工作区，不是本技能包目录）")


def add_root_arg(parser) -> None:
    """给子命令加统一的 --data-root 参数。"""
    parser.add_argument(
        "--data-root", metavar="DIR",
        help="数据根；缺省读 $KM_DATA_ROOT（或 $DESENS_DATA_ROOT），或脚本上级目录（含工作区标记时）")


# ----------------------------------------------------------------------------
# 每个源的三个关键目录
# ----------------------------------------------------------------------------

def work_dir(data_root: str, sid: str, override: str | None = None) -> str:
    """工作副本目录：显式 override 优先，否则 <data_root>/<id>。"""
    if override:
        return os.path.abspath(override)
    return os.path.join(data_root, sid)


def secret_dir(data_root: str, sid: str) -> str:
    """秘密材料与运行态目录：<data_root>/.km/<id>（永不外传）。"""
    return os.path.join(data_root, SECRETS_DIRNAME, sid)


# ----------------------------------------------------------------------------
# 标准子目录（相对秘密材料目录 <data_root>/.km/<id>/）
# ----------------------------------------------------------------------------

def config_dir(root: str) -> str:
    return os.path.join(root, "config")


def vault_dir(root: str) -> str:
    return os.path.join(root, "vault")


def map_dir(root: str) -> str:
    return os.path.join(root, "vault", "map")


def reports_dir(root: str) -> str:
    return os.path.join(root, "reports")


# ---- workspace.json（秘密材料目录内的源指针） ----

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


def workspace_source(root: str) -> str | None:
    """真源路径：读 workspace.json 的 source（**无默认值**，缺失返回 None）。"""
    return load_workspace(root).get("source")


def workspace_mirror(root: str) -> str:
    """工作副本路径：**只认 workspace.json 的 mirror（无默认）**。

    缺 mirror 即报错——调用方应显式传 --mirror / --out，或先跑 guide.py。
    """
    m = load_workspace(root).get("mirror")
    if not m:
        raise SystemExit(
            f"{workspace_path(root)} 里没有 mirror（工作副本路径）。\n"
            "请显式指定 --mirror / --out <工作副本目录>，或先执行："
            "python scripts/guide.py --source <真源目录>")
    return m