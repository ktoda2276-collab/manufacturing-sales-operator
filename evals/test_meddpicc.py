"""評価メトリクスの単体テスト (Phase 10 ①)

設計の核心: ここでは LLM (実 API) を一切呼ばない。metrics.py の計算ロジックを
**合成した predicted dict** で検証する「箱の検証」。これにより:
- 件数ゼロ・コストゼロで機構の正しさを担保 (ゴールド 30 件を待たない)
- テストが決定論的 (LLM の確率的揺れが混入しない = flaky にならない)

実 API でゴールドと突き合わせる「中身の検証」は evals/run_eval.py に分離。

参照: docs/specs/phase10_eval_harness.md §5 (受け入れ基準)
"""
import json

import pytest

from evals import CASES_PATH, PROJECT_ROOT, load_cases, load_gold
from evals.metrics import (
    MEDDPICC_ITEMS,
    VALID_STATUSES,
    boundary_analysis,
    status_match,
)


def _make_record(statuses: dict[str, str]) -> dict:
    """項目→status の指定から、抽出器出力と同形の dict を組み立てる補助。

    evidence の中身は精度計算に使われないのでダミーで埋める。指定しなかった
    項目はデフォルト "confirmed" にする (テストを短く保つため)。
    """
    record: dict[str, dict] = {}
    for item in MEDDPICC_ITEMS:
        record[item] = {
            "status": statuses.get(item, "confirmed"),
            "evidence": {"quote": "", "speaker": "", "interpretation": ""},
        }
    return record


# --- status_match (§3.1) ---------------------------------------------------


def test_status_match_perfect():
    """完全一致なら strict_rate=1.0、全項目 True。"""
    rec = _make_record({})
    result = status_match(rec, rec)
    assert result["strict_rate"] == 1.0
    assert result["match_count"] == 8
    assert result["total"] == 8
    assert all(result["per_item"].values())


def test_status_match_one_off():
    """1 項目だけずらすと strict_rate=7/8、その項目だけ False。"""
    gold = _make_record({})
    pred = _make_record({"metrics": "partial"})  # gold は confirmed
    result = status_match(pred, gold)
    assert result["match_count"] == 7
    assert result["strict_rate"] == pytest.approx(7 / 8)
    assert result["per_item"]["metrics"] is False
    assert result["per_item"]["champion"] is True


def test_status_match_missing_key_counts_as_mismatch():
    """予測にキー欠落があれば不一致扱い (例外にしない)。"""
    gold = _make_record({})
    pred = _make_record({})
    del pred["competition"]
    result = status_match(pred, gold)
    assert result["per_item"]["competition"] is False
    assert result["match_count"] == 7


# --- boundary_analysis (§3.2) ----------------------------------------------


def test_boundary_perfect_match():
    """完全一致なら strict も tolerant も 1.0、不一致リストは空。"""
    rec = _make_record({})
    result = boundary_analysis(rec, rec)
    assert result["strict_rate"] == 1.0
    assert result["tolerant_rate"] == 1.0
    assert result["boundary_mismatches"] == []
    assert result["serious_mismatches"] == []


def test_boundary_partial_unconfirmed_swap_is_tolerated():
    """partial↔unconfirmed の揺れ 1 件: strict 7/8・tolerant 8/8・boundary 1 件。"""
    gold = _make_record({"decision_criteria": "unconfirmed"})
    pred = _make_record({"decision_criteria": "partial"})
    result = boundary_analysis(pred, gold)
    assert result["strict_rate"] == pytest.approx(7 / 8)
    assert result["tolerant_rate"] == pytest.approx(8 / 8)
    assert len(result["boundary_mismatches"]) == 1
    assert result["boundary_mismatches"][0]["item"] == "decision_criteria"
    assert result["serious_mismatches"] == []


def test_boundary_unconfirmed_partial_swap_is_symmetric():
    """逆向き (unconfirmed→partial) でも同様に境界揺れ扱い。"""
    gold = _make_record({"metrics": "partial"})
    pred = _make_record({"metrics": "unconfirmed"})
    result = boundary_analysis(pred, gold)
    assert len(result["boundary_mismatches"]) == 1
    assert result["serious_mismatches"] == []
    assert result["tolerant_rate"] == pytest.approx(1.0)


def test_boundary_confirmed_unconfirmed_swap_is_serious():
    """confirmed→unconfirmed の取り違え: strict 7/8・tolerant 7/8・serious 1 件。"""
    gold = _make_record({"identify_pain": "confirmed"})
    pred = _make_record({"identify_pain": "unconfirmed"})
    result = boundary_analysis(pred, gold)
    assert result["strict_rate"] == pytest.approx(7 / 8)
    assert result["tolerant_rate"] == pytest.approx(7 / 8)
    assert result["boundary_mismatches"] == []
    assert len(result["serious_mismatches"]) == 1
    assert result["serious_mismatches"][0]["item"] == "identify_pain"


def test_boundary_risk_is_always_serious():
    """risk が絡む不一致は境界揺れにならず重大誤り扱い。"""
    gold = _make_record({"competition": "risk"})
    pred = _make_record({"competition": "unconfirmed"})
    result = boundary_analysis(pred, gold)
    assert result["boundary_mismatches"] == []
    assert len(result["serious_mismatches"]) == 1


def test_boundary_mixed_case():
    """境界揺れ 1 + 重大誤り 1 が同時に出るケースの内訳。"""
    gold = _make_record({"metrics": "unconfirmed", "champion": "confirmed"})
    pred = _make_record({"metrics": "partial", "champion": "risk"})
    result = boundary_analysis(pred, gold)
    assert result["strict_rate"] == pytest.approx(6 / 8)
    assert result["tolerant_rate"] == pytest.approx(7 / 8)
    assert len(result["boundary_mismatches"]) == 1
    assert len(result["serious_mismatches"]) == 1


# --- データセット (cases.json + ゴールド) の健全性 -------------------------


def test_cases_file_loads():
    """ケース台帳が読め、最低 1 件あること。"""
    cases = load_cases()
    assert isinstance(cases, list)
    assert len(cases) >= 1


@pytest.mark.parametrize("case", load_cases(), ids=lambda c: c["name"])
def test_case_paths_exist(case):
    """各ケースの議事録・ゴールドのパスが実在すること。"""
    assert (PROJECT_ROOT / case["transcript_path"]).is_file()
    assert (PROJECT_ROOT / case["gold_path"]).is_file()


@pytest.mark.parametrize("case", load_cases(), ids=lambda c: c["name"])
def test_gold_has_eight_valid_items(case):
    """各ゴールドが MEDDPICC 8 項目を漏れなく持ち、status が有効 4 値であること。"""
    gold = load_gold(case)
    assert set(gold.keys()) == set(MEDDPICC_ITEMS)
    for item in MEDDPICC_ITEMS:
        assert gold[item]["status"] in VALID_STATUSES


@pytest.mark.parametrize("case", load_cases(), ids=lambda c: c["name"])
def test_gold_matches_itself_perfectly(case):
    """ゴールドをゴールドと突き合わせれば必ず strict_rate=1.0 (メトリクスの自明性確認)。"""
    gold = load_gold(case)
    assert status_match(gold, gold)["strict_rate"] == 1.0
    assert boundary_analysis(gold, gold)["tolerant_rate"] == 1.0


def test_cases_json_is_valid_json():
    """台帳が壊れた JSON でないこと (回帰防止)。"""
    json.loads(CASES_PATH.read_text(encoding="utf-8"))
