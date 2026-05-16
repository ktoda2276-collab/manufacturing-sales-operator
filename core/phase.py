"""商談フェーズ判定モジュール (Phase 7)

Phase 6 で抽出した MEDDPICC 8 項目の status から、商談の現在フェーズ
(Phase 0〜5) を機械的に算出する。

設計判断:
- 軸 2 (判断は LLM、計算は Python): フェーズ判定は決定論的 Python の責務。
  LLM 不使用。
- 軸 3 (Defense in Depth): 期待収益計算 (core/revenue.py) とは別モジュールに
  分離し、独立してテスト・差し替え可能にする。
- 入力契約は flat dict[str, str]。core/extractor.py の返却値 (ネスト構造)
  からは呼び出し側で {k: v["status"] for k, v in d.items()} で変換する。
  Phase 7 関数の入力をシンプルにすることでテスト性とシリアライズ性を確保。

参照:
- docs/specs/phase7_phase_revenue.md: フェーズ判定ロジックの仕様
- prompts/examples/SPEC.md §6: MEDDPICC キー名・status enum の定義元
"""

# MEDDPICC 8 項目のキー (snake_case, SPEC.md §6.1)
# frozenset で immutable 化、誤改変を防ぐ
MEDDPICC_KEYS = frozenset(
    {
        "metrics",
        "economic_buyer",
        "decision_criteria",
        "decision_process",
        "paper_process",
        "identify_pain",
        "champion",
        "competition",
    }
)

# status enum (SPEC.md §6.2)
VALID_STATUSES = frozenset({"confirmed", "partial", "unconfirmed", "risk"})


def judge_phase(meddpicc_evaluations: dict[str, str]) -> int:
    """MEDDPICC 評価から商談フェーズ (0〜5) を判定する。

    判定規則:
        - confirmed のみカウント (partial, unconfirmed, risk は未到達扱い)
        - 全条件 AND (累積的)
        - identify_pain が confirmed でない場合は Phase 0
        - Phase 1: identify_pain
        - Phase 2: + champion
        - Phase 3: + economic_buyer, metrics
        - Phase 4: + decision_criteria, decision_process, paper_process
        - Phase 5: + competition

    Args:
        meddpicc_evaluations: MEDDPICC 8 項目の status を持つ dict。
            キーは MEDDPICC_KEYS と完全一致、値は VALID_STATUSES のいずれか。

    Returns:
        商談フェーズ (0〜5 の int)。

    Raises:
        ValueError: 入力 dict のキーが 8 項目と一致しない、または値が enum 外。
    """
    if set(meddpicc_evaluations.keys()) != MEDDPICC_KEYS:
        missing = MEDDPICC_KEYS - meddpicc_evaluations.keys()
        extra = meddpicc_evaluations.keys() - MEDDPICC_KEYS
        raise ValueError(
            f"MEDDPICC キーが不正です。missing={sorted(missing)}, "
            f"extra={sorted(extra)}"
        )
    for k, v in meddpicc_evaluations.items():
        if v not in VALID_STATUSES:
            raise ValueError(
                f"status 値が不正です: {k}={v!r} "
                f"(許容: {sorted(VALID_STATUSES)})"
            )

    def _is_confirmed(key: str) -> bool:
        return meddpicc_evaluations[key] == "confirmed"

    # Phase 1 未到達
    if not _is_confirmed("identify_pain"):
        return 0
    # Phase 2 未到達
    if not _is_confirmed("champion"):
        return 1
    # Phase 3 未到達
    if not (_is_confirmed("economic_buyer") and _is_confirmed("metrics")):
        return 2
    # Phase 4 未到達
    if not (
        _is_confirmed("decision_criteria")
        and _is_confirmed("decision_process")
        and _is_confirmed("paper_process")
    ):
        return 3
    # Phase 5 未到達
    if not _is_confirmed("competition"):
        return 4
    return 5


if __name__ == "__main__":
    # 動作確認: 既存 Few-shot サンプル 2 件で受け入れ基準を確認する。
    # 実行コマンド: python -m core.phase
    import json
    from pathlib import Path

    examples_dir = Path(__file__).parent.parent / "prompts" / "examples"
    cases = [
        ("sample_extraction_phase2.json", 2),
        ("sample_extraction_phase4.json", 4),
    ]

    print("Phase 7 judge_phase 動作確認")
    print("=" * 60)
    all_ok = True
    for filename, expected in cases:
        data = json.loads((examples_dir / filename).read_text(encoding="utf-8"))
        flat = {k: v["status"] for k, v in data.items()}
        actual = judge_phase(flat)
        verdict = "OK" if actual == expected else "NG"
        if actual != expected:
            all_ok = False
        print(f"[{verdict}] {filename}")
        print(f"       expected=Phase {expected}, actual=Phase {actual}")
        print(f"       statuses: {flat}")
    print("=" * 60)
    print("動作確認: 全件 OK" if all_ok else "動作確認: 不一致あり")
