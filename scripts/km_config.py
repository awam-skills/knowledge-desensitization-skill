# AI生成
"""km_config.py — 「知识库脱敏」技能的用户级配置读写（两级配置）。

用户 2026-10-22 定稿
- 配置两级：全局 + 每源；每源可覆盖全局。
- 用户配置根 = %USERPROFILE%\\.knowledge-manager-skills（或 $KM_CONFIG_HOME）。
- 配置文件**不进 Git**（该根目录本身不是仓库；其中可能含路径等环境信息）。
- 对话中更新 → 作为下次默认值（即写回这里）。

全局 <KM_CONFIG_HOME>/settings.yaml
    version: 1
    data_root: <数据根>              # 运行产物根；缺省留空，由 guide 在首次配置时确定
    private_backup_dir: <目录>       # 还原材料的单独备份目录（技能执行落盘；建议离线）
    tmp_root: <目录>                 # 临时工作区根（用完即弃）
    defaults:
      sync:     { on_deleted: ask, exclude: [".git/**", ".obsidian/**", ...] }
      sanitize: { include_assets: false, note_dirs: [journals, pages] }
      backup:   { advise: true }

每源 <KM_CONFIG_HOME>/sources/<id>.yaml
    version: 1
    id: logseq-main
    name: logseq                 # 数据集名（工作副本工作目录名；缺省 = id）
    source: G:\\Data\\knowledge\\logseq\\main    # 真源（只读）
    work_dir: <可选，覆盖工作副本位置；缺省 <data_root>/<id>>
    sync:     { on_deleted: ask, exclude: [...] }      # 可选，覆盖全局
    sanitize: { include_assets: false, note_dirs: [...] }
    backup:   { advise: true, private_backup_dir: <可选，覆盖全局> }
    state:    {}                 # 运行态由脚本维护；用户不必手改

安全
- 本模块只读写配置本身；配置中不含任何凭据明文。
"""
from __future__ import annotations

import copy
import os
import re

import yaml

import paths

# ----------------------------------------------------------------------------
# 默认配置
# ----------------------------------------------------------------------------

DEFAULT_SETTINGS: dict = {
    "version": 1,
    "data_root": None,
    "private_backup_dir": None,
    "tmp_root": None,
    "defaults": {
        "sync": {
            "on_deleted": "ask",          # ask | keep | remove
            "exclude": [
                ".git/**", ".git",
                ".obsidian/**", ".obsidian",
                "node_modules/**", "node_modules",
                "__pycache__/**", "__pycache__",
            ],
        },
        "sanitize": {
            "include_assets": False,
            "note_dirs": ["journals", "pages"],
            "all_text": False,
        },
        "backup": {
            "advise": True,
        },
    },
}


# ----------------------------------------------------------------------------
# YAML 读写
# ----------------------------------------------------------------------------

def _load_yaml(path: str) -> dict:
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
    except (OSError, yaml.YAMLError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_yaml(path: str, data: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        yaml.safe_dump(data, fh, allow_unicode=True, sort_keys=False,
                       default_flow_style=False)


def deep_merge(base: dict, override: dict) -> dict:
    """把 override 深度合并进 base 的副本（dict 递归，其余直接覆盖）。"""
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


# ----------------------------------------------------------------------------
# settings.yaml
# ----------------------------------------------------------------------------

def load_settings() -> dict:
    """读全局设置（缺失项以默认值补齐）。"""
    raw = _load_yaml(paths.settings_path())
    return deep_merge(DEFAULT_SETTINGS, raw)


def save_settings(settings: dict) -> None:
    _save_yaml(paths.settings_path(), settings)


def update_settings(patch: dict) -> dict:
    """读→深合并 patch→写回，返回合并后的完整设置。"""
    merged = deep_merge(load_settings(), patch)
    save_settings(merged)
    return merged


# ----------------------------------------------------------------------------
# sources/<id>.yaml
# ----------------------------------------------------------------------------

def list_source_ids() -> list[str]:
    d = paths.sources_dir()
    if not os.path.isdir(d):
        return []
    out = []
    for fn in sorted(os.listdir(d)):
        if fn.endswith((".yaml", ".yml")):
            out.append(fn.rsplit(".", 1)[0])
    return out


def load_source(sid: str) -> dict:
    """读某源配置（含全局默认的合并视图）。"""
    raw = _load_yaml(paths.source_cfg_path(sid))
    if not raw:
        return {}
    return raw


def resolve_source(sid: str) -> dict:
    """返回该源的「生效配置」：全局 defaults 与每源覆盖合并后的视图。

    结构：
      { id, name, source, work_dir, sync:{...}, sanitize:{...}, backup:{...}, state:{} }
    """
    settings = load_settings()
    raw = load_source(sid)
    if not raw:
        raise SystemExit(f"找不到源配置：{paths.source_cfg_path(sid)}\n先执行："
                         f"python scripts/guide.py --source <真源目录>")
    base = {
        "version": 1,
        "id": sid,
        "name": raw.get("name") or sid,
        "source": raw.get("source"),
        "work_dir": raw.get("work_dir"),
        "data_root": raw.get("data_root") or settings.get("data_root"),
        "private_backup_dir": (raw.get("backup", {}) or {}).get("private_backup_dir")
                              or settings.get("private_backup_dir"),
        "sync": settings["defaults"]["sync"],
        "sanitize": settings["defaults"]["sanitize"],
        "backup": settings["defaults"]["backup"],
        "state": raw.get("state", {}) or {},
    }
    for section in ("sync", "sanitize", "backup"):
        if isinstance(raw.get(section), dict):
            base[section] = deep_merge(base[section], raw[section])
    return base


def save_source(sid: str, cfg: dict) -> None:
    _save_yaml(paths.source_cfg_path(sid), cfg)


def effective_tmp_root() -> str:
    """临时工作区根的**生效值**：用户 settings.tmp_root 优先，否则默认 <技能目录>/.tmp。

    目录不存在时按需创建（用完即弃；永不入 Git）。
    """
    root = load_settings().get("tmp_root") or paths.default_tmp_root()
    return os.path.abspath(root)


def global_config_dir() -> str:
    """用户级「全局配置」目录（与 settings.yaml 同级，不进 Git、不随技能发布）。

    用于存放全局的 rules.json / whitelist.json / exclude.json / literals.json，
    作为所有源的默认；每源 `<data_root>/.km/<id>/config/` 可覆盖。
    """
    return os.path.join(paths.km_config_home(), "config")


def list_sources() -> list[dict]:
    """列出所有已保存的源（供入口询问「要处理哪个」）。读不动的源会被跳过。"""
    out: list[dict] = []
    for sid in list_source_ids():
        try:
            c = resolve_source(sid)
        except SystemExit:
            continue
        st = c.get("state") or {}
        out.append({
            "id": sid,
            "name": c.get("name"),
            "source": c.get("source"),
            "work_dir": c.get("work_dir"),
            "data_root": c.get("data_root"),
            "private_backup_dir": c.get("private_backup_dir"),
            "last_sync": st.get("last_sync"),
        })
    return out


def write_source_state(sid: str, updates: dict) -> None:
    """更新某源的 state 段（运行态；用户不必手改）。"""
    raw = load_source(sid) or {"version": 1, "id": sid}
    st = raw.setdefault("state", {})
    st.update(updates)
    save_source(sid, raw)


# ----------------------------------------------------------------------------
# 由「输入源路径」推导 id / 数据集名 / 数据根子目录
# ----------------------------------------------------------------------------

_SLUG_RX = re.compile(r"[^A-Za-z0-9._\-]+")


def _slug(s: str) -> str:
    return _SLUG_RX.sub("-", s).strip("-") or "source"


def suggest_subdir(source: str) -> str:
    """按输入源路径推荐数据根下的子目录名：取末两级路径拼接。

    例：G:\\Data\\knowledge\\logseq\\main  →  logseq-main
         G:\\Knowledge\\logseq             →  Knowledge-logseq
    """
    p = os.path.abspath(source).rstrip("\\/")
    parts = [x for x in re.split(r"[\\/]+", p) if x]
    tail = parts[-2:] if len(parts) >= 2 else parts
    return _slug("-".join(tail))


def suggest_id(source: str) -> str:
    """推荐源 id（= 数据根子目录名的安全 slug）。"""
    return _slug(suggest_subdir(source))


def suggest_name(source: str) -> str:
    """推荐数据集名（默认取源目录叶子名）。"""
    leaf = os.path.basename(os.path.abspath(source).rstrip("\\/")) or "notes"
    return _slug(leaf)