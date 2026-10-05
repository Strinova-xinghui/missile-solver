# -*- coding: utf-8 -*-
"""数据宿主：`DATA_DIR` 从哪来、内核宿主目录怎么物化。

**为什么需要这一层**（不是"多余的一层包装"）：内核 `wt_missile.py` 自己写死了

    ROOT = Path(__file__).resolve().parent
    RAW  = ROOT / 'inputs/resources/2.59.0.28'      # import 时就读 presets.json + manifest.json

也就是说**内核只认"数据放在我旁边"**，没有环境变量、也没有路径参数（读一遍内核就能确认）。
而本包要满足两条硬要求：① 内核**逐字节不改**（否则 `solver.SOLVER_SHA256` 身份判据失效）；
② 数据**不落仓、不随包**，位置由 `DATA_DIR` 给。

⇒ 折中办法：在**可写的宿主目录**里物化一份"内核 + 指向 `DATA_DIR` 的联接"：

    <MISSILE_SOLVER_HOME 或缓存目录>/missile_solver/<内核 sha 前 12 位>/
        wt_missile.py                     ← 逐字节复制（哈希仍等于 SOLVER_SHA256）
        inputs/resources/2.59.0.28 → DATA_DIR   ← 目录联接（JOIN/symlink）；不行就复制

这样内核看到的仍是"数据在我旁边"，`DATA_DIR` 也真的生效，而 site-packages 一个字节都不用改。
⚠ `import missile_solver` **不需要**数据；只有真要用内核（跑工况 / 读 blk）时才要求 `DATA_DIR`。
"""

from __future__ import annotations

import hashlib
import os
import shutil
import sys
from pathlib import Path

#: 钉死的内核与数据版本（与内核自报的 `wt_missile.VERSION` 一致）。
VERSION = "2.59.0.28"
KERNEL_FILE = "wt_missile.py"
#: 内核源码的 SHA-256（**与 `solver.SOLVER_SHA256` 同一个值**）：物化时校验，别让错的副本上桌。
KERNEL_SHA256 = "dad09caea2c31e0ef26a8327f3bb96c50f83b91bcb29d6b05616f23faba2f730"

PACKAGE_DIR = Path(__file__).resolve().parent
#: 包内自带的内核源码（发布物的一部分；**不含数据**）。
KERNEL_DIR = PACKAGE_DIR / "kernel"

#: 数据目录的环境变量名（`data/README.md` 与 `scripts/fetch_data.py` 都用它）。
DATA_ENV = "DATA_DIR"
#: 宿主目录的环境变量名（可选；不给就用用户缓存目录）。
HOME_ENV = "MISSILE_SOLVER_HOME"
#: 数据目录里必须有的三样（缺一个就报错，别让内核去撞）。
REQUIRED = ("manifest.json", "presets.json")


class DataNotConfigured(RuntimeError):
    """`DATA_DIR` 没设 / 不存在 / 内容不成套 —— 一律给出"怎么弄到数据"的可读指引。"""


def data_help() -> str:
    """缺数据时的统一说明（报错原文与 README 用同一份口径）。"""
    return (
        f"这个包**不分发游戏数据**（数据权利归 Gaijin）。\n"
        f"  1) 设环境变量：$env:{DATA_ENV} = \"<放数据的目录>\"（Linux/macOS：export {DATA_ENV}=...）；\n"
        f"     该目录里应**直接**有 {REQUIRED[0]} / {REQUIRED[1]} / 168 份 *.blk（内核读的 BLK 文本，\n"
        f"     由 War Thunder 的只读解包器从 aces.vromfs.bin 产出；**不是** Datamine 仓里的 JSON *.blkx）；\n"
        f"  2) 取数据 / 校验数据：python scripts/fetch_data.py --help（含上游 tag {VERSION} 的 sparse clone\n"
        f"     与逐文件 sha256 校验）；\n"
        f"  3) 详见本仓 data/README.md。")


def cache_root() -> Path:
    """宿主目录的根：`MISSILE_SOLVER_HOME` 优先，否则平台缓存目录。"""
    env = os.environ.get(HOME_ENV)
    if env:
        return Path(env).expanduser().resolve()
    if sys.platform.startswith("win"):
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("TEMP") or "."
    else:
        base = os.environ.get("XDG_CACHE_HOME") or (Path.home() / ".cache")
    return Path(base).expanduser().resolve() / "missile-solver"


def kernel_home() -> Path:
    """物化用的宿主目录（**只依赖内核哈希，不依赖 `DATA_DIR`** ⇒ import 期可用）。"""
    return cache_root() / "kernel" / KERNEL_SHA256[:12]


#: `spec.VENDOR_DIR` 就是这个（`solver.py` 用它拼内核文件路径与 `inputs/...`）。
KERNEL_HOME = kernel_home()


def data_dir(*, required: bool = True) -> Path | None:
    """读 `DATA_DIR`。`required=True` 时不合规**当场抛**（报错带 `data_help()`）。"""
    raw = (os.environ.get(DATA_ENV) or "").strip()
    if not raw:
        if required:
            raise DataNotConfigured(f"{DATA_ENV} 没设（或为空）。\n{data_help()}")
        return None
    p = Path(raw).expanduser()
    if not p.is_dir():
        if required:
            raise DataNotConfigured(f"{DATA_ENV}={raw!r} 不是目录。\n{data_help()}")
        return None
    if required:
        missing = [n for n in REQUIRED if not (p / n).is_file()]
        n_blk = len(list(p.glob("*.blk")))
        if missing or n_blk == 0:
            raise DataNotConfigured(
                f"{DATA_ENV}={raw!r} 里缺 {missing or '任何 *.blk'}（现有 .blk {n_blk} 份）。\n"
                f"{data_help()}")
    return p


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _make_dir_link(link: Path, target: Path) -> bool:
    """把 `link` 指到 `target`（目录）。返回是否成功；**不抛异常**（失败就走复制）。"""
    if link.is_symlink() or (hasattr(link, "is_junction") and link.is_junction()):
        return True
    try:
        os.symlink(target, link, target_is_directory=True)      # POSIX / Windows 开发者模式
        return True
    except (OSError, NotImplementedError, AttributeError):
        pass
    if sys.platform.startswith("win"):
        try:                                                    # 目录联接：**不需要管理员**
            r = __import__("subprocess").run(
                ["cmd", "/c", "mklink", "/J", str(link), str(target)],
                stdout=__import__("subprocess").DEVNULL, stderr=__import__("subprocess").DEVNULL)
            return r.returncode == 0 and link.is_dir()
        except Exception:                                       # noqa: BLE001
            return False
    return False


def _copy_resources(src: Path, dst: Path) -> int:
    """联接不可用时的退路：把数据复制进宿主目录（2.8 MB 量级，代价可接受）。"""
    dst.mkdir(parents=True, exist_ok=True)
    n = 0
    for f in src.iterdir():
        if f.is_file():
            shutil.copy2(f, dst / f.name)
            n += 1
    return n


def ensure_kernel_home(*, verify: bool = True) -> Path:
    """把"内核 + 数据联接"物化好并返回宿主目录（幂等，可反复调用）。"""
    home = KERNEL_HOME
    home.mkdir(parents=True, exist_ok=True)

    # ① 内核：逐字节复制并校验哈希（**绝不放一份哈希不符的副本上去**）
    src_kernel = KERNEL_DIR / KERNEL_FILE
    if not src_kernel.is_file():
        raise RuntimeError(f"包内缺内核源码：{src_kernel}（安装不完整？）")
    if verify and _sha256(src_kernel) != KERNEL_SHA256:
        raise RuntimeError(f"包内内核哈希不符：{src_kernel}（期望 {KERNEL_SHA256}）")
    dst_kernel = home / KERNEL_FILE
    if not dst_kernel.is_file() or (verify and _sha256(dst_kernel) != KERNEL_SHA256):
        shutil.copy2(src_kernel, dst_kernel)

    # ② 数据：DATA_DIR 里那套（缺就抛，带指引）
    src_data = data_dir()
    res_dir = home / "inputs" / "resources"
    res_dir.mkdir(parents=True, exist_ok=True)
    link = res_dir / VERSION
    if link.is_symlink() or (hasattr(link, "is_junction") and link.is_junction()):
        if link.resolve() == src_data.resolve():
            return home
        link.unlink()                                   # 指向别处 ⇒ 换掉（只删我们自己建的联接）
    if link.exists() and not link.is_dir():
        link.unlink()
    if not link.exists():
        if not _make_dir_link(link, src_data):
            _copy_resources(src_data, link)
    # ③ 最后核一眼：内核 import 时会自己按 manifest.json 校验 presets.json，这里先挡住明显的缺件
    for name in REQUIRED:
        if not (link / name).is_file():
            raise DataNotConfigured(
                f"宿主目录里缺 {name}：{link / name}\n{data_help()}")
    return home
