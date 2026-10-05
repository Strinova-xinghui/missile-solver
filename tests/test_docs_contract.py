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


def test_version_and_tier_section_covers_the_version_gate():
    """用户反复问的四件（版本门 / 档位真值表 / `pro` 归宿 / 升版本清单）必须有落盘的一节。

    判据刻意分两层：关键词在**全文**里（够用即可被发现）与在 **§8 正文**里（防止散落各处混过去）。
    README 必须指向这一节 —— 否则第三方读到契约也找不到这张真值表。
    """
    text = DOC.read_text(encoding="utf-8")
    assert "版本与档位" in text, "缺「版本与档位」一节"
    keys = ("默认时间步", "PROFILES_BY_VERSION", "ELF_BY_VERSION", "差分对拍", "CACHE_SCHEMA", "pro")
    missing = [k for k in keys if k not in text]
    assert not missing, f"「版本与档位」缺关键词：{missing}"

    parts = text.split("## 8. 版本与档位", 1)
    assert len(parts) == 2, "「版本与档位」不是以 `## 8.` 标题给出的"
    body8 = parts[1]
    for k in ("默认时间步", "PROFILES_BY_VERSION", "ELF_BY_VERSION", "差分对拍", "CACHE_SCHEMA"):
        assert k in body8, f"{k} 不在 §8 正文里"
    # 两轴真值表要给全四种组合（四行里都提到档位名）
    assert "standard" in body8 and "fast" in body8
    assert "2.59.0.28" in body8 and "2.59.0.22" in body8
    assert "差分对拍" in body8 and "外推" in body8, "没写验证的边界（对拍只覆盖探针工况集）"

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "版本与档位" in readme, "README 没有指向「版本与档位」一节"
