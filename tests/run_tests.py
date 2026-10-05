"""零依赖测试运行器（pytest 不可用时的替代，语法与 pytest 兼容）。

用法：
    python tests/run_tests.py                 # 跑全部
    python tests/run_tests.py -k batch        # 只跑名字包含 batch 的用例
    python tests/run_tests.py -l              # 列出用例
    python tests/run_tests.py --strict        # 缺游戏数据时**不许**降级成跳过（CI 全量 job 用）

支持的 pytest 子集：模块级 test_* 函数、fixture（按名字注入，支持 fixture 调用 fixture）、
@pytest.mark.parametrize 参数化、pytest.raises / pytest.approx / pytest.skip
（由 tests/_pytest_shim.py 提供）。

⚠ **游戏数据不在位时不会整片变红**：`vendor/wt-missile/inputs/` 里那 168 份 War Thunder `.blk`
权利归 Gaijin、不随本仓库分发，缺了它所有**需要求解器**的用例都会抛 `SolverUnavailable`。
既然那不是代码缺陷，运行器就进入**降级模式**：逐个打印一行 `SKIP …`、计数与"通过"**分开报**、
退出码仍为 0，并在文末给出按模块的跳过汇总。`--strict` 关掉降级（缺数据 ⇒ 照旧失败），
CI 的 self-hosted 全量 job 强制用它，免得"148 项被跳过"被误读成全绿。
判定口径与放回数据的步骤见 `tests/_vendor_data.py` 与 `vendor/README.md`。
"""

from __future__ import annotations

import argparse
import importlib.util
import inspect
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import _vendor_data  # noqa: E402  （tests/ 刚插进 sys.path）


def _make_pytest_shim():
    """尽量用真 pytest；不可用时注册 tests/_pytest_shim.py。"""
    try:
        import pytest  # noqa: F401

        return
    except ModuleNotFoundError:
        pass
    import _pytest_shim

    sys.modules["pytest"] = _pytest_shim  # type: ignore[assignment]


def _load_module(path: Path):
    """按文件路径加载测试模块。

    **必须先注册进 `sys.modules`** —— `dataclasses` 解析 `@dataclass` 时会用
    `cls.__module__` 回查 `sys.modules`，模块不在里面就抛
    `AttributeError: 'NoneType' object has no attribute '__dict__'`，
    而且是在**收集阶段**炸掉整个测试跑（看起来像某个测试文件的语法问题，其实不是）。
    加载失败时把半成品摘掉，避免污染后续同名模块。
    """
    spec = importlib.util.spec_from_file_location(path.stem, path)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[path.stem] = mod
    try:
        spec.loader.exec_module(mod)
    except BaseException:
        sys.modules.pop(path.stem, None)
        raise
    return mod


def _fixture_factories(mod):
    import pytest

    out = {}
    for name, obj in vars(mod).items():
        if callable(obj) and getattr(obj, "_dsh_is_fixture", False):
            out[name] = obj
    return out


def _resolve_fixture(name, factories, cache, stack=(), mod_name=""):
    if name in cache:
        return cache[name]
    if name not in factories:
        raise KeyError(f"未定义的 fixture: {name}")
    if name in stack:
        raise RuntimeError(f"fixture 循环依赖: {stack + (name,)}")
    fn = factories[name]
    scope = getattr(fn, "_dsh_fixture_scope", "function")
    skey = (mod_name, scope, name)
    if scope != "function" and skey in _SCOPED:
        return _SCOPED[skey]
    kwargs = {p: _resolve_fixture(p, factories, cache, stack + (name,), mod_name)
              for p in _params(fn)}
    gen = fn(**kwargs)
    if inspect.isgenerator(gen):  # 支持 yield fixture
        value = next(gen)
        cache[name] = value
        if scope == "function":
            _GENERATORS.append(gen)
        else:                                   # 收尾推迟到作用域结束（见 _teardown_scope）
            _SCOPED_GENS.append((mod_name, gen))
        _SCOPED[skey] = value
        return value
    cache[name] = value = gen
    _SCOPED[skey] = value
    return value


#: 函数级 yield fixture 的收尾发生器（每个用例结束就收）。
_GENERATORS: list = []
#: 非函数级（module/session）fixture 的值与收尾发生器 —— **跨用例复用**。
_SCOPED: dict = {}
_SCOPED_GENS: list = []


def _teardown_scope(mod_name=None):
    """收尾某个模块的 module/session 级 yield fixture（`None` = 全部）。

    `run_small`（`scope="module"`）的收尾会删掉 `results/workflow_pytest*`；必须在**该模块
    最后一个用例之后**才收，否则后面的用例找不到产物。
    """
    while _SCOPED_GENS and (mod_name is None or _SCOPED_GENS[-1][0] == mod_name):
        _m, gen = _SCOPED_GENS.pop()
        try:
            next(gen)
        except StopIteration:
            pass
        except Exception:  # noqa: BLE001
            traceback.print_exc()


def _params(fn):
    return [p.name for p in inspect.signature(fn).parameters.values()]


def collect(test_dir: Path):
    """返回 [(module_name, test_name, fn, kwargs, params, factories)]；conftest.py 的 fixture 作为全局 fixture。"""
    global_fixtures = {}
    conftest = test_dir / "conftest.py"
    if conftest.exists():
        _load_module(conftest)  # 触发 sys.path 引导
        global_fixtures = _fixture_factories(_load_module(conftest))

    cases = []
    for path in sorted(test_dir.glob("test_*.py")):
        mod = _load_module(path)
        factories = dict(global_fixtures)
        factories.update(_fixture_factories(mod))
        for name, obj in vars(mod).items():
            if not name.startswith("test_") or not callable(obj):
                continue
            paramsets = getattr(obj, "_dsh_paramsets", None)
            if paramsets is None:
                paramsets = [{}]
            params = [p for p in _params(obj) if p not in paramsets[0]]
            for extra in paramsets:
                cases.append((path.stem, f"{name}{_suffix(extra)}", obj, extra, params, factories))
    return cases


def _suffix(kwargs):
    if not kwargs:
        return ""
    inner = "-".join(f"{k}={v}" for k, v in kwargs.items())
    return f"[{inner}]"


def _is_skip(exc: BaseException) -> bool:
    """是不是 `pytest.skip()`（真 pytest 的 Skipped 继承 BaseException，故按**类名**认）。"""
    return type(exc).__name__ == "Skipped"


def _skip_reason(exc: BaseException) -> str:
    reason = str(exc).strip() or type(exc).__name__
    return " ".join(reason.split())[:200]


def _print_degrade_banner(strict: bool) -> None:
    print("=" * 70)
    print(f"⚠ {_vendor_data.missing_reason()}")
    if strict:
        print("  **--strict 生效**：依赖它的用例会照旧记 FAIL、退出码 1（CI 全量口径）。")
    else:
        print("  降级模式：依赖它的用例记 SKIP（不算通过、也不算失败，退出码仍为 0）。")
        print(f"  放回方法见 {_vendor_data.HOWTO}；想改成失败：加 --strict。")
    print("=" * 70)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("-k", dest="filter", default=None, help="只运行名字包含该子串的用例")
    ap.add_argument("-l", "--list", action="store_true", help="列出用例后退出")
    ap.add_argument("--strict", action="store_true",
                    help="缺求解器/游戏数据时**不降级**：需要的用例照旧记失败")
    ap.add_argument("--dir", default=str(Path(__file__).resolve().parent))
    args = ap.parse_args(argv)

    _make_pytest_shim()
    cases = collect(Path(args.dir))
    if args.filter:
        cases = [c for c in cases if args.filter in c[1] or args.filter in c[0]]

    if args.list:
        for mod, name, *_ in cases:
            print(f"{mod}::{name}")
        print(f"\n共 {len(cases)} 个用例")
        return 0

    # 缺内核/游戏数据 ⇒ 降级：把"因为数据不在而抛的异常"记成 SKIP，而不是一片红。
    missing = not _vendor_data.ready()
    degrade = missing and not args.strict
    if missing:
        _print_degrade_banner(args.strict)

    n_pass = n_fail = n_skip = 0
    failures = []
    skips: dict[str, int] = {}
    t_start = time.perf_counter()
    cur_mod = None
    for mod_name, name, fn, extra, params, factories in cases:
        if mod_name != cur_mod:                     # 模块边界：收掉上一个模块的作用域 fixture
            if cur_mod is not None:
                _teardown_scope(cur_mod)
            cur_mod = mod_name
        cache = {}
        try:
            kwargs = dict(extra)
            for p in _params(fn):
                if p in kwargs:
                    continue
                kwargs[p] = _resolve_fixture(p, factories, cache, (), mod_name)
            fn(**kwargs)
            n_pass += 1
            print(f"PASS {mod_name}::{name}")
        except BaseException as exc:  # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            if _is_skip(exc) or degrade:
                # 显式 pytest.skip()，或"数据缺席"的降级 —— 两者都不算失败，但**也不算通过**。
                n_skip += 1
                skips[mod_name] = skips.get(mod_name, 0) + 1
                detail = _skip_reason(exc) or _vendor_data.missing_reason()
                if not _is_skip(exc):
                    detail = f"{type(exc).__name__}: {detail}"
                print(f"SKIP {mod_name}::{name}: {detail}")
                continue      # 下面三条 FAIL 行保持**原来的缩进**（纯缩进差异会被 git -w 吃掉）
            n_fail += 1
            failures.append((mod_name, name, exc, traceback.format_exc()))
            print(f"FAIL {mod_name}::{name}: {type(exc).__name__}: {exc}")
        finally:
            while _GENERATORS:  # 结束**函数级** yield fixture（模块级留到模块边界）
                gen = _GENERATORS.pop()
                try:
                    next(gen)
                except StopIteration:
                    pass
                except Exception:  # noqa: BLE001
                    traceback.print_exc()
    _teardown_scope()
    wall = time.perf_counter() - t_start

    print("\n" + "=" * 70)
    for mod_name, name, exc, tb in failures:
        print(f"\n--- {mod_name}::{name} ---\n{tb.rstrip()}")
    print("=" * 70)
    tail = f"通过 {n_pass} / 失败 {n_fail} / 共 {n_pass + n_fail}，用时 {wall:.1f} s"
    if n_skip:
        # 摘要行**分开报**跳过：跳过 ≠ 通过（退出码只看 n_fail）。
        tail = (f"通过 {n_pass} / 跳过 {n_skip} / 失败 {n_fail} / "
                f"共 {n_pass + n_skip + n_fail}，用时 {wall:.1f} s")
        top = "、".join(f"{m} {n}" for m, n in sorted(skips.items(), key=lambda kv: -kv[1]))
        print(f"跳过明细（按模块）：{top}")
        if degrade:
            print(f"⚠ 降级运行：这 {n_skip} 项**没有被执行**（跳过 ≠ 通过）。"
                  f"{_vendor_data.missing_reason()}；放回方法见 {_vendor_data.HOWTO}。")
    print(tail)
    return 1 if n_fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
