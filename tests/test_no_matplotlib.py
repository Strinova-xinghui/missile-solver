# -*- coding: utf-8 -*-
"""④ 纯求解器：`import missile_solver` **不许**带进 matplotlib，也不许带 GUI/出图那一堆模块。

这条要在**干净的解释器**里查（`sys.modules` 会被同进程里别的用例污染），所以用子进程。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"

#: 抽取时**故意不带**的模块（现有仓里有，本包没有；列全了免得哪天悄悄抄回来）。
MUST_NOT_EXIST = (
    "figures", "webui", "webui_static", "cli", "workflow", "grid", "record",
    "traj", "traj3d", "fig3", "fig3scan", "targets", "report", "engine", "presets",
)
#: 更宽的第三方禁区：本包运行期只需要 numpy。
FORBIDDEN_TOP = ("matplotlib", "mpl_toolkits", "PIL", "pandas", "scipy", "skimage")

_PROBE = r"""
import json, sys
sys.path.insert(0, r"{src}")
import missile_solver
mods = sorted(sys.modules)
print(json.dumps({{
    "version": missile_solver.__version__,
    "forbidden_loaded": [m for m in {forbidden!r} if m in sys.modules],
    "leaked": sorted(set(mods) & set("missile_sim " .split())),
    "has_figures": __import__("importlib.util", fromlist=["x"]).find_spec("missile_solver.figures") is not None,
}}))
"""


def _probe() -> dict:
    code = _PROBE.format(src=str(SRC), forbidden=list(FORBIDDEN_TOP))
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                         encoding="utf-8", timeout=120)
    assert out.returncode == 0, out.stderr[-800:]
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_import_does_not_pull_matplotlib_or_pandas():
    got = _probe()
    assert got["forbidden_loaded"] == [], f"这些不该被导入：{got['forbidden_loaded']}"
    assert got["has_figures"] is False, "包里出现了 missile_solver.figures —— 出图层不该被抽进来"


def test_import_does_not_need_the_upstream_package():
    got = _probe()
    assert got["leaked"] == [], f"泄漏了上游包名：{got['leaked']}"
    assert got["version"] == "0.1.0"


def test_extraction_scope_is_exactly_what_we_asked_for():
    """包目录里只该有：抽来的 7 个模块 + `_data.py` + `kernel/` + 类型标记。"""
    pkg = SRC / "missile_solver"
    mods = sorted(p.stem for p in pkg.glob("*.py"))
    assert mods == ["__init__", "_data", "bg", "catalog", "mapping", "pool", "solver", "spec"], mods
    for name in MUST_NOT_EXIST:
        assert not (pkg / f"{name}.py").exists(), f"不该抽 {name}.py"
    assert (pkg / "py.typed").is_file()
    assert (pkg / "kernel" / "wt_missile.py").is_file()


def test_no_matplotlib_in_declared_dependencies():
    """`pyproject.toml` 的**依赖数组**里不许有 matplotlib（注释里提到它是可以的）。

    用 `tomllib` 解析，别用"字符串里有这个词"这种脆判据（注释里就出现过这个词）。
    """
    import tomllib

    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    deps = data["project"]["dependencies"]
    assert deps, "依赖数组不该是空的（内核要 numpy）"
    joined = " ".join(deps).lower()
    assert "matplotlib" not in joined, deps
    assert any(d.lower().startswith("numpy") for d in deps), deps
    assert data["project"]["name"] == "missile-solver"
    assert data["project"]["license"] == "AGPL-3.0-only"
    assert data["project"]["requires-python"] == ">=3.12,<3.15"
