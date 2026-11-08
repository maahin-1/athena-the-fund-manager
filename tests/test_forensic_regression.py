from __future__ import annotations

import json

import numpy as np
import pytest

from athena.forensic.statements import regression as rg


def rows(n: int = 1500, slope: float = 0.0, seed: int = 3, outlier: bool = False) -> list[dict]:
    rng = np.random.default_rng(seed)
    out = []
    for i in range(n):
        elevated = int(rng.choice([0, 1, 2, 3, 4], p=[0.5, 0.25, 0.15, 0.07, 0.03]))
        size, vol, computed = int(rng.integers(0, 5)), int(rng.integers(0, 3)), int(rng.integers(2, 14))
        y = slope * elevated + 0.02 * size + float(rng.normal(0, 0.4))
        out.append({"symbol": f"S{i}", "elevated": elevated, "computed": computed, "size_fifth": size, "vol_third": vol, "excess_return": y, "unexplained_break": False})
    if outlier:
        out[0]["excess_return"] = 40.0  # a stock that rose forty-fold
    return out


def test_a_true_negative_slope_is_found_with_an_interval_below_zero_and_the_reading_says_supported() -> None:
    res = rg.regress(rows(slope=-0.08), draws=300)
    assert res["B_winsorised_primary"]["slope"] == pytest.approx(-0.08, abs=0.03) and res["B_winsorised_primary"]["ci95"][1] < 0
    assert res["C_rank_check"]["ci95"][1] < 0 and res["reading"] == "supported at this date"
    assert res["by_elevated"]["0"]["companies"] > res["by_elevated"]["3+"]["companies"] > 0


def test_no_effect_gives_an_interval_over_zero_and_the_smallest_slope_the_sample_could_see() -> None:
    res = rg.regress(rows(slope=0.0), draws=300)
    lo, hi = res["B_winsorised_primary"]["ci95"]
    assert lo < 0 < hi and res["reading"] == "not shown at this date"
    seen = res["B_winsorised_primary"]["smallest_slope_seen"]
    assert seen == pytest.approx(2.8 * res["B_winsorised_primary"]["bootstrap_se"], abs=1e-4) and 0 < seen < 0.2


def test_a_positive_slope_is_read_as_opposite_not_as_supported() -> None:
    assert rg.regress(rows(slope=0.1), draws=300)["reading"] == "opposite at this date"


def test_one_huge_outlier_moves_the_raw_slope_but_not_the_winsorised_or_the_rank_one() -> None:
    clean = rg.regress(rows(slope=-0.08), draws=100)
    spiked = rg.regress(rows(slope=-0.08, outlier=True), draws=100)
    assert abs(spiked["A_raw"]["slope"] - clean["A_raw"]["slope"]) > 0.005  # the raw slope moves with a single forty-fold stock
    assert spiked["B_winsorised_primary"]["slope"] == pytest.approx(clean["B_winsorised_primary"]["slope"], abs=0.01)
    assert spiked["C_rank_check"]["slope"] == pytest.approx(clean["C_rank_check"]["slope"], abs=0.005)


def test_ranks_share_the_mean_rank_of_ties_and_winsorising_clips_both_ends() -> None:
    assert list(rg.ranks(np.array([5.0, 1.0, 1.0, 3.0]))) == pytest.approx([1.0, 0.5 / 3, 0.5 / 3, 2 / 3])  # the two ones share positions 0 and 1: 0.5 of 3
    y = np.arange(1000, dtype=float)
    w = rg.winsorise(y, 0.01)
    assert w.min() == pytest.approx(9.99) and w.max() == pytest.approx(989.01)


def test_the_design_has_one_dummy_per_level_above_the_base_and_the_coverage_bins_are_the_binary_tests() -> None:
    x, names = rg.design(rows(50))
    assert names == ["const", "elevated", "cov1", "cov2", "size1", "size2", "size3", "size4", "vol1", "vol2"] and x.shape == (50, 10)
    assert [rg.cov_of(n) for n in (0, 3, 4, 7, 8, 14)] == [0, 0, 1, 1, 2, 2]


def test_too_few_companies_is_not_computed() -> None:
    assert "not computable" in rg.regress(rows(100))["reading"]


def test_loading_drops_rows_with_no_return_no_volatility_or_an_unexplained_break(tmp_path) -> None:  # noqa: ANN001
    base = {"elevated": 1, "computed": 9, "size_fifth": 2, "vol_third": 1, "excess_return": 0.1, "unexplained_break": False}
    saved = {"as_of": "2025-07-01", "end": "2026-11-01", "rows": [
        {**base, "symbol": "A"}, {**base, "symbol": "B", "excess_return": None}, {**base, "symbol": "C", "vol_third": None}, {**base, "symbol": "D", "unexplained_break": True},
    ]}
    path = tmp_path / "bt.json"
    path.write_text(json.dumps(saved), encoding="utf-8")
    header, kept = rg.load(path)
    assert header == {"as_of": "2025-07-01", "end": "2026-11-01"} and [r["symbol"] for r in kept] == ["A"]


def test_the_command_reads_two_dates_and_says_replicated_only_when_both_are_supported(tmp_path, capsys) -> None:  # noqa: ANN001
    for name, slope in (("a.json", -0.1), ("b.json", 0.0)):
        (tmp_path / name).write_text(json.dumps({"as_of": "2025-01-01", "end": "2026-05-01", "rows": rows(slope=slope)}), encoding="utf-8")
    assert rg.main([str(tmp_path / "a.json"), str(tmp_path / "b.json")]) == 0
    out = json.loads(capsys.readouterr().out)
    assert [r["reading"] for r in out["results"]] == ["supported at this date", "not shown at this date"] and out["replicated"] is False and "overlap" in out["caution"]
