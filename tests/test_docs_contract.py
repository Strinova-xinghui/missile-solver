# -*- coding: utf-8 -*-
"""文档契约：`docs/SOLVER-API.md`（第三方要读的接口面）必须在、且覆盖关键契约。

这条是"防误删"用的：契约文档没了，别人装了包也不知道 `Scene`/`Shot` 长什么样。
同时在读 README 时确认它**指向**这份文档。
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "SOLVER-API.md"

#: 文档里必须出现的关键词（契约的骨架；少一个就说明文档被削了）。
KEYS = ("Scene", "Shot", "_CF_COLUMNS", "档位", "只动一个文件", "ΔV", "β", "68",
        "standard", "fast")
#: 镜像适配的硬线：**不许**带回主仓的层级路径（外壳/出图/页面那些不在本包）。
FORBIDDEN = ("src/missile_sim", "missile_sim/", "webui", "workflow", "figures",
             "results/", "run_plane")


def test_solver_api_doc_exists_and_covers_the_contract():
    assert DOC.is_file(), f"接口契约文档不见了：{DOC}"
    text = DOC.read_text(encoding="utf-8")
    missing = [k for k in KEYS if k not in text]
    assert not missing, f"契约文档缺关键词：{missing}"
    # 68 列那条要真的是"68"
    assert "68 列" in text or "68列" in text, "文档里没有 68 列的表述"


def _body(text: str) -> str:
    """去掉开头的引文块（`> …`）：那一段是**镜像说明**，它必须点名"搬走了哪些主仓引用"。"""
    return "\n".join(l for l in text.splitlines() if not l.lstrip().startswith(">"))


def test_doc_has_no_main_repo_layers_or_absolute_paths():
    """契约正文里不许出现主仓层级引用（镜像说明那段除外）与任何绝对路径。"""
    text = DOC.read_text(encoding="utf-8")
    body = _body(text)
    leaked = [b for b in FORBIDDEN if b in body]
    assert not leaked, f"文档正文带回了主仓层级引用：{leaked}"
    for bad in ("C:\\", "E:\\", "D:\\", "/home/", "/Users/"):
        assert bad not in text, f"文档里出现了绝对路径片段 {bad!r}"


def test_readme_points_to_the_contract_doc_and_states_the_scope():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "docs/SOLVER-API.md" in readme, "README 没有指向接口契约文档"
    assert "只提供数据与计算" in readme, "README 没有说明本包的边界（只提供数据与计算）"
