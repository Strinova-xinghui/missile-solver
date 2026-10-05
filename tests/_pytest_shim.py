"""pytest 的最小替身：只实现本项目用到的 fixture / parametrize / raises / approx。

仅在没有安装真 pytest 时由 tests/run_tests.py 注册为 sys.modules["pytest"]。
"""

from __future__ import annotations

import math


class _Approx:
    def __init__(self, expected, rel=None, abs=None):
        self.expected = expected
        self.rel = rel
        self.abs = abs

    def _tolerance(self):
        exp = self.expected
        rel = self.rel if self.rel is not None else 1e-6
        abs_ = self.abs if self.abs is not None else 1e-12
        try:
            return max(abs_, rel * abs(exp))
        except TypeError:
            return abs_

    def __eq__(self, other):
        if isinstance(self.expected, (list, tuple)):
            return list(other) == [self.expected[i] for i in range(len(self.expected))]
        try:
            return math.isclose(float(other), float(self.expected), rel_tol=self.rel if self.rel is not None else 1e-6,
                                abs_tol=self.abs if self.abs is not None else 1e-12)
        except (TypeError, ValueError):
            return other == self.expected

    def __repr__(self):
        return f"approx({self.expected!r}, rel={self.rel}, abs={self.abs})"


def approx(expected, rel=None, abs=None):
    return _Approx(expected, rel=rel, abs=abs)


class _Raises:
    def __init__(self, exc_type, match=None):
        self.exc_type = exc_type
        self.match = match
        self.value = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            raise AssertionError(f"期望抛出 {self.exc_type.__name__}，实际没有异常")
        if issubclass(exc_type, self.exc_type):
            self.value = exc
            return True
        return False


def raises(exc_type, match=None):
    return _Raises(exc_type, match=match)


class _Mark:
    @staticmethod
    def parametrize(argnames, argvalues, **kwargs):
        names = [a.strip() for a in (argnames.split(",") if isinstance(argnames, str) else list(argnames))]

        def decorator(fn):
            paramsets = []
            for values in argvalues:
                vals = values if isinstance(values, (tuple, list)) else (values,)
                paramsets.append(dict(zip(names, vals)))
            fn._dsh_paramsets = paramsets
            return fn

        return decorator

    def __getattr__(self, item):  # 其他 mark 一律当作无操作
        def decorator(fn=None, **kwargs):
            if fn is None:
                return lambda f: f
            return fn

        return decorator


mark = _Mark()


def fixture(fn=None, *, scope="function", **kwargs):
    """fixture 装饰器。`scope` 会被记下来 —— 运行器按它决定复用范围。

    ⚠ 记 `scope` 是必须的：`scope="module"` 的 fixture（如 workflow 的 `run_small`，跑一次
    完整工作流要几十秒）如果不复用，就会被**每个用到它的用例各跑一遍**。
    """
    def wrap(func):
        func._dsh_is_fixture = True
        func._dsh_fixture_scope = str(scope or "function")
        return func

    if fn is not None:
        return wrap(fn)
    return wrap


class Skipped(Exception):
    """`pytest.skip()` 的信号。

    ⚠ 运行器按**类名** `Skipped` 识别它，而不是 `isinstance`：真 pytest 的
    `_pytest.outcomes.Skipped` 继承 `BaseException`（不是 `Exception`），两边都要认。
    """

    def __init__(self, reason=""):
        super().__init__(str(reason) or "skipped")
        self.reason = str(reason) or "skipped"


def skip(reason=""):
    """跳过当前用例（打印一行说明，不计入通过、也不计入失败）。

    本仓库目前只有 `tests/_vendor_data.py:require()` 用它：游戏数据不在位时跳过依赖它的用例。
    """
    raise Skipped(reason)


def fail(reason=""):  # pragma: no cover - 等价于断言失败，保留给将来用例
    raise AssertionError(reason)
