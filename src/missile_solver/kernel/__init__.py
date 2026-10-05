# -*- coding: utf-8 -*-
"""内核原样副本（`wt_missile.py`）。**不要改这个文件** —— 它的 SHA-256 是包的硬身份：

    solver.SOLVER_SHA256 == _data.KERNEL_SHA256 == sha256(kernel/wt_missile.py)

改它就必须同时改那两处常量（并说明为什么）。它由 `_data.ensure_kernel_home()` 复制到宿主目录，
与 `DATA_DIR` 里的数据放在一起后被 `solver.load_solver()` import。
"""
