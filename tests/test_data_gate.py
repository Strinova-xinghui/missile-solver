# -*- coding: utf-8 -*-
"""数据闸门判据：哈希表两份一致、`verify_data()` 有牙（好数据过、坏数据拒）。

* 表一致性那条**不需要数据**；
* 验数据那两条需要 `DATA_DIR`（没有就 SKIP —— 数据缺席不是失败、也不算通过）。
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _p in (ROOT / "src", ROOT / "tests"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import pytest  # noqa: E402

from missile_solver import _data                                    # noqa: E402

GOLD_MANIFEST_SHA256 = "23328d6c"          # 交付包 manifest.json 的前 8 位（本轮快照）
GOLD_N_BLK = 168
GOLD_N_REQUIRED = 169                      # 168 blk + presets.json


def test_repo_table_and_packaged_table_are_the_same_bytes():
    """仓库 `data/checksums.json` 与包内那份必须**逐字节相同**（否则装完核的是另一份表）。"""
    a = ROOT / "data" / "checksums.json"
    b = _data.PACKAGE_DIR / "data" / "checksums.json"
    assert a.is_file() and b.is_file(), (a, b)
    assert a.read_bytes() == b.read_bytes(), "两份哈希表不一致"
    assert _data.checksums_path().read_bytes() == a.read_bytes()


def test_table_shape_and_no_absolute_paths():
    """表的形状 + 不含本机绝对路径（用户口径：仓里不出现本机绝对路径）。"""
    raw = (ROOT / "data" / "checksums.json").read_text(encoding="utf-8")
    t = json.loads(raw)
    assert t["counts"] == {"required": GOLD_N_REQUIRED, "blk": GOLD_N_BLK, "optional": 3}
    assert len(t["files"]) == GOLD_N_REQUIRED
    assert len([k for k in t["files"] if k.endswith(".blk")]) == GOLD_N_BLK
    assert t["manifest_sha256"].startswith(GOLD_MANIFEST_SHA256), t["manifest_sha256"]
    assert t["version"] == "2.59.0.28"
    # 允许出现"上游仓 URL"，但**不许**出现本机盘符路径或 /home/<user> 这类绝对路径
    for bad in ("C:\\", "E:\\", "D:\\", "/home/", "/Users/"):
        assert bad not in raw, f"哈希表里出现了绝对路径片段 {bad!r}"
    assert t["kernel_data"]["archive_basename"] == "aces.vromfs.bin"


def test_verify_data_passes_on_the_real_data_and_reports_the_counts():
    """真数据必须过闸门，且**没有硬失败**（`⚠` 提醒可以有，但那不算失败）。"""
    ok, problems = _data.verify_data()
    hard = [p for p in problems if not p.startswith(("⚠", "（"))]
    assert ok, problems
    assert hard == [], hard


def test_verify_data_has_teeth(tmp_path):
    """反证：改一个字节 ⇒ 必失败且**指出是哪个文件**；缺 `presets.json` ⇒ 也必失败。"""
    src = _data.data_dir()
    if src is None:                                                  # pragma: no cover
        pytest.skip("DATA_DIR 没设")
    work = tmp_path / "data"
    work.mkdir()
    # 只拷"够判据用"的一份子集：省时间，也足以证明闸门在看内容
    names = sorted(json.loads((ROOT / "data" / "checksums.json").read_text(encoding="utf-8"))
                   ["files"])[:5] + ["manifest.json", "presets.json"]
    for n in names:
        shutil.copy2(src / n, work / n)
    ok, problems = _data.verify_data(work)
    # 子集 ⇒ 缺件必然被报出来（说明它在逐条比对，而不是"目录存在就算过"）
    landed = sorted(p.name for p in work.iterdir())
    assert not ok and any("缺件" in p for p in problems), \
        f"ok={ok!r} 落了 {len(landed)} 个文件 {landed} problems={problems}"

    # 把其中一份 blk 改一个字节 ⇒ 报"哈希不符"并点名该文件
    victim = sorted(work.glob("*.blk"))[0]
    with open(victim, "ab") as fh:
        fh.write(b"\n# tampered\n")
    ok2, problems2 = _data.verify_data(work)
    assert not ok2
    assert any("哈希不符" in p and victim.name in p for p in problems2), problems2

    # 删掉 manifest.json ⇒ 缺件计数必须**恰好 +1**（证明 manifest.json 也在逐条核对的集合里；
    # ⚠ 别去断言"提示文字里有 manifest.json"：缺件提示只印前 8 个名字，会抄近路抄错）
    import re
    before = re.search(r"缺件 (\d+) 条", "\n".join(problems2))
    (work / "manifest.json").unlink()
    ok3, problems3 = _data.verify_data(work)
    after = re.search(r"缺件 (\d+) 条", "\n".join(problems3))
    assert not ok3, problems3
    assert before and after, (problems2, problems3)
    assert int(after.group(1)) == int(before.group(1)) + 1, (before.group(1), after.group(1))


def test_verify_data_never_lies_about_a_full_good_directory(tmp_path):
    """完整数据（若本机有）走一遍：`ok=True`，且必核条数 == 表里的条数。"""
    src = _data.data_dir()
    if src is None:                                                  # pragma: no cover
        pytest.skip("DATA_DIR 没设")
    ok, problems = _data.verify_data(src)
    assert ok, problems
    n_blk = len(list(src.glob("*.blk")))
    assert n_blk == GOLD_N_BLK, n_blk
    assert hashlib.sha256((src / "manifest.json").read_bytes()).hexdigest().startswith(
        GOLD_MANIFEST_SHA256)
