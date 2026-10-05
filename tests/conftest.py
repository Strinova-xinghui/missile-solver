"""测试包路径引导 + 共享 fixture：让 pytest 免安装即可 import missile_sim。"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

try:  # 有真 pytest 时用它的 fixture 装饰器
    import pytest
except ModuleNotFoundError:  # pragma: no cover
    import _pytest_shim as pytest  # type: ignore[no-redef]


@pytest.fixture
def tmp_path():
    """最小化 tmp_path fixture。

    默认在项目根的 .tmp_test 下建目录（沙箱允许写工作区，系统临时目录可能被拒），
    可用环境变量 MSIM_TEST_TMPDIR 覆盖。
    """
    import os
    import shutil
    import uuid

    base = Path(os.environ.get("MSIM_TEST_TMPDIR", ROOT / ".tmp_test"))
    d = base / f"case_{uuid.uuid4().hex[:8]}"
    d.mkdir(parents=True, exist_ok=True)
    try:
        yield d
    finally:
        shutil.rmtree(d, ignore_errors=True)

