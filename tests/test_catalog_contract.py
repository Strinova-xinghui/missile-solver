# -*- coding: utf-8 -*-
"""③ 目录契约：内核 168 预设 → 目录里 159 可算 / 9 条「需要动态解算」+ 11 弹池逐值同源。

数字与文案**从现有仓的判据抄来**（`tests/test_catalog.py`：`N_PRESETS=168`、
`N_UNCOMPUTABLE=9`、`UNCOMPUTABLE_KEYS`、`reason == "需要动态解算"`）。
"""

from __future__ import annotations

import copy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _p in (ROOT / "src", ROOT / "tests"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import pytest  # noqa: E402

from missile_solver import bg, catalog, pool, solver        # noqa: E402

N_PRESETS = 168
N_UNCOMPUTABLE = 9
UNCOMPUTABLE_KEYS = (
    "cn_pl15", "cn_pl15_default",
    "us_aim_54a", "us_aim_54a_default", "us_aim_54b",
    "us_aim_54c", "us_aim_54c_default", "us_aim_54c_plus", "us_aim_54c_plus_default",
)


@pytest.fixture(scope="module")
def rec():
    return catalog.build()


def test_catalog_covers_every_kernel_preset(rec):
    names = list(solver.preset_names())
    assert len(names) == N_PRESETS, f"内核预设数变了：{len(names)}"
    assert rec["schema"] == catalog.SCHEMA
    assert rec["count"] == len(names)
    seen = {m["native"] for m in rec["missiles"]} | {u["native"] for u in rec["unsupported"]}
    assert seen == set(names)
    assert rec["computable"] == len(rec["missiles"]) == N_PRESETS - N_UNCOMPUTABLE
    assert sum(1 for m in rec["missiles"] if m["in_pool11"]) == len(pool.POOL) == 11


def test_unsupported_entries_carry_the_user_facing_reason(rec):
    bad = rec["unsupported"]
    assert len(bad) == N_UNCOMPUTABLE
    assert sorted(u["key"] for u in bad) == sorted(UNCOMPUTABLE_KEYS)
    for u in bad:
        assert u["reason"] == "需要动态解算", u          # 面用户的文案（上游 2026-10-05 口径）
        assert "propulsion" in str(u.get("kernel_reason") or ""), u   # 内核原话另存
        assert u["in_pool11"] is False


def test_pool11_values_equal_the_existing_implementations(rec):
    """目录**不是**第二套数：11 弹池的 ΔV/BC 必须等于 `mapping.blk_metrics`、β/γ 等于 `bg.point_of`。"""
    rows = {m["native"]: m for m in rec["missiles"]}
    for e in pool.POOL:
        metrics = pool.metrics_of(e)
        point = bg.point_of(e)
        row = rows[e.native]
        assert row["dv"] == metrics.dv, e.key
        assert row["bc"] == metrics.bc, e.key
        assert row["gamma"] == point.gamma, e.key
        assert row["ginv"] == bg.ginv_of(point.gamma), e.key
        assert row["key"] == e.key and row["full_name"] == e.full_name


def test_verify_has_teeth(rec):
    """反证：改一个数 / 删一条 / 改 schema ⇒ `catalog.verify()` 必须当场炸。"""
    first = pool.POOL[0].native

    tampered = copy.deepcopy(rec)
    for m in tampered["missiles"]:
        if m["native"] == first:
            m["dv"] += 1.0
    with pytest.raises(catalog.CatalogError):
        catalog.verify(tampered, full=False)

    truncated = copy.deepcopy(rec)
    truncated["missiles"] = [m for m in truncated["missiles"] if m["native"] != first]
    with pytest.raises(catalog.CatalogError):
        catalog.verify(truncated, full=False)

    bumped = copy.deepcopy(rec)
    bumped["schema"] = catalog.SCHEMA + 1
    with pytest.raises(catalog.CatalogError):
        catalog.verify(bumped, full=False)


def test_catalog_round_trips_through_json(tmp_path, rec):
    path = catalog.write_json(tmp_path / catalog.NAME)
    got = catalog.load(path)
    strip = lambda d: {k: v for k, v in d.items() if k != "generated_utc"}   # noqa: E731
    assert strip(got) == strip(rec)
    catalog.verify(got, full=False)
    assert got["generated_utc"].endswith("+00:00")
