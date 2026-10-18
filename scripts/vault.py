# AI生成
"""vault：用 age 加密/解密原文存档与映射表。

子命令
    keygen   生成 age 密钥对（私钥写在 vault/keys/，公钥用于加密）
    encrypt  打包并加密（默认只收文本笔记，图片附件不入档）
    decrypt  解密到指定目录
    list     列出 vault 内的加密产物

安全
- 私钥文件 vault/keys/age-key.txt 必须离线备份一份，丢失即无法恢复存档。
- 中间明文 zip 写在系统临时目录，加密后立即删除。

目录无关
- 全局限定 --data-root；vault/ 落在 <data-root>/vault/。
- encrypt 的 --src 可用环境无关路径；缺省读 workspace.json 的 source。
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sanitize_lib import iter_files, read_text  # noqa: E402
import paths  # noqa: E402

AGE_CANDIDATES = ["age", r"S:\Libs\Scoop\shims\age.exe",
                  r"S:\Libs\Scoop\shims\age-keygen.exe"]


def _which(kind: str = "age") -> str:
    for c in AGE_CANDIDATES:
        if kind in c and (os.path.isabs(c) and os.path.exists(c) or shutil.which(c)):
            return c
    found = shutil.which(kind)
    if found:
        return found
    raise FileNotFoundError(f"找不到 {kind}，请先 scoop install age")


def _key_dir(root: str) -> str:
    return os.path.join(root, "vault", "keys")


def _key_file(root: str) -> str:
    return os.path.join(_key_dir(root), "age-key.txt")


def _raw_dir(root: str) -> str:
    return os.path.join(root, "vault", "raw")


def _read_pub(root: str) -> str:
    kf = _key_file(root)
    if not os.path.exists(kf):
        return ""
    with open(kf, encoding="utf-8") as fh:
        for line in fh:
            if line.startswith("# public key:"):
                return line.split(":", 1)[1].strip()
    return ""


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def cmd_keygen(args) -> int:
    root = args._root
    kdir, kfile = _key_dir(root), _key_file(root)
    os.makedirs(kdir, exist_ok=True)
    if os.path.exists(kfile):
        print("已存在私钥：", kfile)
        print("公钥：", _read_pub(root))
        return 0
    exe = _which("age-keygen")
    r = subprocess.run([exe, "-o", kfile], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(r.stderr)
    os.chmod(kfile, 0o600)
    print("已生成私钥：", kfile)
    print("公钥：", _read_pub(root))
    print("\n★ 请立刻把 %s 离线备份（U 盘/密码管理器），丢失即无法解密存档。" % kfile)
    return 0


def _build_zip(src: str, zpath: str, text_only: bool) -> tuple[int, int]:
    n = total = 0
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        if os.path.isfile(src):
            z.write(src, os.path.basename(src))
            return 1, os.path.getsize(src)
        for p in iter_files(src, include_runtime=False, include_generated=False):
            if text_only:
                t = read_text(p)
                if t is None:
                    continue
                z.writestr(os.path.relpath(p, src).replace("\\", "/"),
                           t.encode("utf-8"))
                total += len(t.encode("utf-8"))
            else:
                z.write(p, os.path.relpath(p, src).replace("\\", "/"))
                total += os.path.getsize(p)
            n += 1
    return n, total


def cmd_encrypt(args) -> int:
    root = args._root
    pub = _read_pub(root)
    if not pub:
        print("缺少公钥，请先执行 keygen", file=sys.stderr)
        return 2
    src = args.src or paths.default_source(root)
    if not src:
        print("缺源目录：给 --src，或在 <data-root>/workspace.json 配置 source",
              file=sys.stderr)
        return 2
    rdir = _raw_dir(root)
    os.makedirs(rdir, exist_ok=True)
    ts = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    name = args.name or os.path.basename(src.rstrip("\\/")) or "payload"
    out = os.path.join(rdir, f"{name}-{ts}.zip.age")

    tmpdir = tempfile.mkdtemp(prefix="vault-")
    zpath = os.path.join(tmpdir, "payload.zip")
    try:
        n, total = _build_zip(src, zpath, text_only=not args.with_assets)
        age = _which("age")
        r = subprocess.run([age, "-r", pub, "-o", out, zpath],
                           capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(r.stderr)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    manifest = {
        "created_at": dt.datetime.now().isoformat(timespec="seconds"),
        "source": src,
        "archive": out,
        "entries": n,
        "plain_bytes": total,
        "archive_bytes": os.path.getsize(out),
        "recipient": pub,
        "sha256": _sha256(out),
        "text_only": not args.with_assets,
    }
    mpath = os.path.join(rdir, "manifest.json")
    old = []
    if os.path.exists(mpath):
        with open(mpath, encoding="utf-8") as fh:
            old = json.load(fh).get("archives", [])
    old.append(manifest)
    with open(mpath, "w", encoding="utf-8") as fh:
        json.dump({"archives": old}, fh, ensure_ascii=False, indent=2)

    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


def cmd_decrypt(args) -> int:
    root = args._root
    age = _which("age")
    os.makedirs(args.out, exist_ok=True)
    tmpdir = tempfile.mkdtemp(prefix="vault-")
    zpath = os.path.join(tmpdir, "payload.zip")
    try:
        r = subprocess.run([age, "-d", "-i", _key_file(root), "-o", zpath, args.input],
                           capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(r.stderr)
        with zipfile.ZipFile(zpath) as z:
            z.extractall(args.out)
            names = z.namelist()
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
    print("已解密", len(names), "项 →", args.out)
    return 0


def cmd_list(args) -> int:
    rdir = _raw_dir(args._root)
    if not os.path.isdir(rdir):
        print("(vault/raw 不存在)")
        return 0
    for f in sorted(os.listdir(rdir)):
        p = os.path.join(rdir, f)
        print(f"{os.path.getsize(p):>12,}  {f}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    paths.add_root_arg(ap)
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("keygen").set_defaults(fn=cmd_keygen)

    e = sub.add_parser("encrypt")
    e.add_argument("--src", help="要打包的目录/文件；缺省读 workspace.json 的 source")
    e.add_argument("--name")
    e.add_argument("--with-assets", action="store_true",
                   help="连同图片等附件一起打包（默认只打包文本笔记）")
    e.set_defaults(fn=cmd_encrypt)

    d = sub.add_parser("decrypt")
    d.add_argument("--input", required=True)
    d.add_argument("--out", required=True)
    d.set_defaults(fn=cmd_decrypt)

    sub.add_parser("list").set_defaults(fn=cmd_list)

    args = ap.parse_args()
    # 注：--data-root 需放在子命令之前（argparse 全局选项）
    args._root = paths.resolve_data_root(getattr(args, "data_root", None))
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())