# -*- coding: utf-8 -*-
"""`missile_solver` —— War Thunder ARH 主动弹的**纯求解器**包（无 GUI / 无出图 / 无产物）。

从 `E:\\导弹包线图-release` 抽取（源提交见本仓 README）；内核 `kernel/wt_missile.py` 逐字节保留。
`import missile_solver` **不需要游戏数据**；只有真要用内核时才要求 `DATA_DIR`（见 `data/README.md`）。
"""

from . import bg, catalog, mapping, pool, solver, spec            # noqa: F401
from .solver import SolverUnavailable                            # noqa: F401
from ._data import DataNotConfigured, data_dir                   # noqa: F401
from .spec import Scene                                          # noqa: F401

__all__ = ["bg", "catalog", "mapping", "pool", "solver", "spec",
           "Scene", "SolverUnavailable", "DataNotConfigured", "data_dir", "run"]

__version__ = "0.1.0"


def run(scene=None, *, missile: str, tier: str = "standard", **kw):
    """最短路径：`missile_solver.run(missile="cn_pl12")` → `Shot`。

    等价于 `solver.run(Scene() if scene is None else scene, missile=..., tier=...)`；
    只是把"要自己 import spec.Scene"这一步省掉（README 的那一行示例用它）。
    """
    return solver.run(spec.Scene() if scene is None else scene,
                      missile=missile, tier=tier, **kw)
