#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""数据工具：**取上游 JSON（可自动）** + **核对内核要的那套 BLK 数据（硬闸门）**。

两件事分开做，理由见 `data/README.md` §2（实测过）：

* 上游 `gszabi99/War-Thunder-Datamine` 的 `.../rocketguns/*.blkx` 是 **JSON**；
* 内核要的是**解包器产出的 BLK 文本**（`gamedata__weapons__rocketguns__<name>.blk`）；
* 两者是同一份数据的两种序列化，**字节不同**（同一枚弹 29,345 B JSON vs 20,972 B BLK）。

所以本脚本**不自己发明解包算法**：

    --fetch-datamine    partial clone（--filter=blob:none）+ sparse-checkout 取那一个目录，
                        写 PROVENANCE.json（repo/tag/commit/逐文件 sha256）。**给核对用，不给内核吃。**
    --verify DIR        拿 data/checksums.json 的 172 条 sha256 逐条比对 DIR（内核要的那套），
                        **任何一条不符/缺件/多件 ⇒ 非零退出**。

用法：
    python scripts/fetch_data.py --fetch-datamine --out .\\datamine
    python scripts/fetch_data.py --verify <放 168 份 blk 的目录>
    python scripts/fetch_data.py --explain          # 只打印"数据从哪来"
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
CHECKSUMS = REPO / "data" / "checksums.json"

#: 钉死的上游（与 data/checksums.json 的 `upstream_datamine` 同源）。
UPSTREAM = "https://github.com/gszabi99/War-Thunder-Datamine"
TAG = "2.59.0.28"
COMMIT = "6d41baa6c1ab31a4bc2232e3ed24b0a2b9efb90c"
SPARSE_PATH = "aces.vromfs.bin_u/gamedata/weapons/rocketguns"


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_table() -> dict:
    if not CHECKSUMS.is_file():
        sys.exit(f"✗ 找不到哈希表：{CHECKSUMS}")
    return json.loads(CHECKSUMS.read_text(encoding="utf-8"))


def explain() -> int:
    t = load_table()
    kd, up = t["kernel_data"], t["upstream_datamine"]
    print("内核要的数据（BLK 文本）：")
    print(f"  清单来源     : {t['source_of_hashes']}")
    print(f"  版本         : {t['version']}    必须 {t['counts']['required']} 条"
          f"（其中 *.blk {t['counts']['blk']}）+ manifest.json；"
          f"可选辅助件 {t['counts']['optional']} 条")
    print(f"  口径         : {t.get('scope_note')}")
    print(f"  游戏归档     : {kd['archive']}")
    print(f"  归档 sha256  : {kd['archive_sha256']}")
    print(f"  解包器       : {kd['extractor']}   sha256 {kd['extractor_sha256']}")
    print(f"  ⚠ 说明       : {kd['note']}")
    print("\n上游 Datamine（JSON，仅供核对出处）：")
    print(f"  {up['repo']}  tag {up['tag']}  commit {up['commit']}")
    print(f"  路径 {up['path']}   （{up['format']}）")
    print("\n步骤：① `--fetch-datamine` 取 JSON 做出处核对 → ② 用解包器产出 BLK 放进 DATA_DIR"
          " → ③ `--verify <DIR>` 过哈希 → ④ `$env:DATA_DIR=<DIR>` 后即可求解。")
    return 0


def verify(directory: Path) -> int:
    """逐条比对哈希（内核真正要的那套数据）。任何不符/缺件 ⇒ 退出码 1。

    分三组（口径见 `data/checksums.json` 的 `scope_note`）：
    * `files`：168 份 `*.blk` + `presets.json` ⇒ **必须逐条相符**；
    * `manifest.json`：不在它自己的 files 表里 ⇒ 单独记 `manifest_sha256`，必须相符；
    * `optional_files`：解包器侧 3 个辅助件 ⇒ 有就核、没有不算失败。
    """
    t = load_table()
    want = t["files"]
    optional = t.get("optional_files") or {}
    if not directory.is_dir():
        sys.exit(f"✗ {directory} 不是目录")
    missing, mismatched = [], []
    for name, digest in sorted(want.items()):
        p = directory / name
        if not p.is_file():
            missing.append(name)
            continue
        got = sha256(p)
        if got != digest:
            mismatched.append((name, digest, got))
    man_bad = None
    man = directory / "manifest.json"
    if not man.is_file():
        missing.append("manifest.json")
    elif sha256(man) != t["manifest_sha256"]:
        man_bad = (t["manifest_sha256"], sha256(man))
    opt_ok, opt_bad, opt_absent = 0, [], []
    for name, digest in sorted(optional.items()):
        p = directory / name
        if not p.is_file():
            opt_absent.append(name)
        elif sha256(p) == digest:
            opt_ok += 1
        else:
            opt_bad.append(name)
    known = set(want) | {"manifest.json"} | set(optional)
    extra = sorted(p.name for p in directory.glob("*") if p.is_file() and p.name not in known)
    n_blk = len(list(directory.glob("*.blk")))

    print(f"目录：{directory}")
    print(f"  必须 {len(want)} 条（*.blk {t['counts']['blk']}）+ manifest.json；"
          f"实际 *.blk {n_blk} 份")
    if man_bad:
        print(f"  ✗ manifest.json 哈希不符：期望 {man_bad[0]}\n      实际 {man_bad[1]}")
    if mismatched:
        print(f"  ✗ 哈希不符 {len(mismatched)} 条：")
        for name, a, b in mismatched[:10]:
            print(f"      {name}\n        期望 {a}\n        实际 {b}")
    if missing:
        print(f"  ✗ 缺件 {len(missing)} 条：{missing[:10]}{' …' if len(missing) > 10 else ''}")
    if opt_bad:
        print(f"  ✗ 可选件哈希不符：{opt_bad}")
    if opt_ok or opt_absent:
        print(f"  · 可选辅助件：相符 {opt_ok} 个、缺席 {len(opt_absent)} 个"
              f"（内核不读，不算失败）：{opt_absent}")
    if extra:
        print(f"  ⚠ 多出来的文件 {len(extra)} 个（不影响内核，但说明目录不干净）："
              f"{extra[:10]}{' …' if len(extra) > 10 else ''}")
    if mismatched or missing or man_bad or opt_bad:
        print("✗ 校验未通过：这份数据**不是**本包钉的那个 2.59.0.28 快照。"
              "（从 Datamine 下的是 JSON，永远过不了这道闸门 —— 见 data/README.md §2）")
        return 1
    print(f"OK：{len(want) + 1}/{len(want) + 1} 条哈希一致 ✓"
          f"（168 份 blk + presets.json + manifest.json，确实是内核要的那套 {t['version']} 数据）")
    return 0


def fetch_datamine(out: Path, *, tag: str = TAG, commit: str = COMMIT,
                   keep_clone: bool = False) -> int:
    """partial clone + sparse-checkout 取上游 rocketguns/*.blkx（JSON 形态）。"""
    if shutil.which("git") is None:
        sys.exit("✗ 需要 git（本步是 partial clone；不想联网就用 --verify 核对已有数据）")
    work = out / "clone"
    work.parent.mkdir(parents=True, exist_ok=True)
    if work.exists():
        shutil.rmtree(work)
    print(f"① partial clone（--filter=blob:none，只取树）→ {work}")
    run = ["git", "clone", "--filter=blob:none", "--no-checkout", "--depth", "1",
           "--branch", tag, UPSTREAM, str(work)]
    if subprocess.run(run).returncode != 0:
        sys.exit("✗ clone 失败（网络/代理？）")
    got = subprocess.run(["git", "-C", str(work), "rev-parse", "HEAD"],
                         capture_output=True, text=True).stdout.strip()
    print(f"   HEAD = {got}（钉的是 {commit}）")
    if got and got != commit:
        print(f"   ⚠ tag {tag} 现在指向 {got}，与 checksums.json 记的 {commit} 不同 —— 记下来。")
    print(f"② sparse-checkout：{SPARSE_PATH}/*.blkx")
    for cmd in (["git", "-C", str(work), "sparse-checkout", "init", "--no-cone"],
                ["git", "-C", str(work), "sparse-checkout", "set",
                 f"{SPARSE_PATH}/*.blkx"],
                ["git", "-C", str(work), "checkout"]):
        if subprocess.run(cmd).returncode != 0:
            sys.exit(f"✗ {cmd[3:]} 失败")
    src = work / SPARSE_PATH
    blkx = sorted(src.glob("*.blkx"))
    if not blkx:
        sys.exit(f"✗ clone 里没有 *.blkx（路径 {SPARSE_PATH} 变了？）")
    dst = out / "blkx"
    dst.mkdir(parents=True, exist_ok=True)
    hashes = {}
    for p in blkx:
        shutil.copy2(p, dst / p.name)
        hashes[p.name] = sha256(p)
    prov = {
        "repo": UPSTREAM, "tag": tag, "pinned_commit": commit, "commit": got or commit,
        "path": f"{SPARSE_PATH}/*.blkx", "format": "JSON（内核不读；仅供出处/字段核对）",
        "count": len(blkx), "files": dict(sorted(hashes.items())),
        "note": ("这是 Datamine 的 JSON 序列化。内核要的是解包器产出的 BLK 文本，"
                 "两者字节不同（见 data/README.md §2）⇒ 本目录**不能**直接当 DATA_DIR。"),
    }
    (out / "PROVENANCE.json").write_text(json.dumps(prov, ensure_ascii=False, indent=1) + "\n",
                                         encoding="utf-8")
    print(f"③ 抄出 {len(blkx)} 份 *.blkx → {dst}")
    print(f"   PROVENANCE.json → {out / 'PROVENANCE.json'}")
    if not keep_clone:
        shutil.rmtree(work, ignore_errors=True)
        print("   已删掉中间 clone（--keep-clone 可留）")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="内核数据：取上游 JSON + 核对 172 条 sha256",
                                 formatter_class=argparse.RawDescriptionHelpFormatter,
                                 epilog=__doc__.split("用法：", 1)[-1].strip())
    ap.add_argument("--fetch-datamine", action="store_true",
                    help="partial clone 上游 rocketguns/*.blkx（JSON，出处核对用）")
    ap.add_argument("--out", type=Path, default=Path("datamine"),
                    help="--fetch-datamine 的落点（默认 ./datamine）")
    ap.add_argument("--tag", default=TAG, help=f"上游 tag（默认 {TAG}）")
    ap.add_argument("--commit", default=COMMIT, help="钉死的 commit（默认检查不匹配会告警）")
    ap.add_argument("--keep-clone", action="store_true", help="保留中间 clone 目录")
    ap.add_argument("--verify", type=Path, default=None,
                    help="核对这个目录里的 168 份 blk + presets.json + manifest.json")
    ap.add_argument("--explain", action="store_true", help="打印数据出处与步骤后退出")
    args = ap.parse_args(argv)

    if args.explain or not (args.fetch_datamine or args.verify):
        return explain()
    rc = 0
    if args.fetch_datamine:
        rc |= fetch_datamine(args.out, tag=args.tag, commit=args.commit,
                             keep_clone=args.keep_clone)
    if args.verify:
        rc |= verify(args.verify)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
