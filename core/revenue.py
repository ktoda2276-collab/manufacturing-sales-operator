"""期待収益計算モジュール (Phase 7)

商談金額・フェーズ・MEDDPICC 評価から、期待収益を機械的に算出する。

設計判断:
- 軸 1 (可逆 vs 不可逆): v1 では固定確率 (フェーズ別 0.1〜0.9)。
  v3 で過去実績ベースの動的確率に置換可能なよう、定数は PHASE_BASE_PROBABILITY
  に集約。
- 軸 2 (判断は LLM、計算は Python): 受注確率の計算は決定論的 Python の責務。
- 軸 3 (Defense in Depth): フェーズ判定 (core/phase.py) とは別モジュールに
  分離し、独立してテスト・差し替え可能にする。
- factors を返却 dict に含める理由: Phase 8 以降のレポート画面で「なぜこの
  期待収益か」を分解表示するため。計算の説明可能性を確保。

参照:
- docs/specs/phase7_phase_revenue.md: 期待収益計算式の仕様
- core/phase.py: judge_phase() でフェーズを得てから本関数に渡す想定
"""
from core.phase import MEDDPICC_KEYS, VALID_STATUSES

# フェーズ別基本確率 (SPEC §期待収益計算)
# Phase 0 は MEDDPICC 情報が不十分な状態、確率 0 とする。
PHASE_BASE_PROBABILITY = {
    0: 0.0,
    1: 0.1,
    2: 0.3,
    3: 0.5,
    4: 0.7,
    5: 0.9,
}

# risk 1 件あたりの減衰係数。risk N 件で 0.7^N。
RISK_DECAY_FACTOR = 0.7


def calc_expected_revenue(
    deal_amount: int,
    phase: int,
    meddpicc_evaluations: dict[str, str],
) -> dict:
    """期待収益 = deal_amount × 受注確率 を計算する。

    受注確率 = フェーズ基本確率 × MEDDPICC 補正 × risk 減衰
        - フェーズ基本確率: PHASE_BASE_PROBABILITY
        - MEDDPICC 補正  : confirmed の数 / 8
        - risk 減衰      : RISK_DECAY_FACTOR ^ (risk の数)

    Args:
        deal_amount: 商談金額 (円)。0 以上の int。
        phase: 商談フェーズ (0〜5)。judge_phase() の戻り値を渡す想定。
        meddpicc_evaluations: MEDDPICC 8 項目の status を持つ flat dict。
            キーは MEDDPICC_KEYS と完全一致、値は VALID_STATUSES のいずれか。

    Returns:
        計算結果 dict (詳細は docs/specs/phase7_phase_revenue.md 参照):
            {
              "deal_amount": int, "phase": int,
              "probability": float, "expected_revenue": int,
              "factors": { phase_base, meddpicc_correction, risk_decay,
                           confirmed_count, risk_count }
            }

    Raises:
        ValueError: deal_amount が負、phase が範囲外、meddpicc_evaluations の
                    キー/値が不正な場合。
    """
    if deal_amount < 0:
        raise ValueError(f"deal_amount は 0 以上必須: {deal_amount}")
    if phase not in PHASE_BASE_PROBABILITY:
        raise ValueError(
            f"phase は 0〜5 必須: {phase} "
            f"(許容: {sorted(PHASE_BASE_PROBABILITY.keys())})"
        )
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

    confirmed_count = sum(
        1 for v in meddpicc_evaluations.values() if v == "confirmed"
    )
    risk_count = sum(1 for v in meddpicc_evaluations.values() if v == "risk")

    phase_base = PHASE_BASE_PROBABILITY[phase]
    meddpicc_correction = confirmed_count / 8
    risk_decay = RISK_DECAY_FACTOR**risk_count

    probability = phase_base * meddpicc_correction * risk_decay
    expected_revenue = round(deal_amount * probability)

    return {
        "deal_amount": deal_amount,
        "phase": phase,
        "probability": probability,
        "expected_revenue": expected_revenue,
        "factors": {
            "phase_base": phase_base,
            "meddpicc_correction": meddpicc_correction,
            "risk_decay": risk_decay,
            "confirmed_count": confirmed_count,
            "risk_count": risk_count,
        },
    }


if __name__ == "__main__":
    # 動作確認: 既存 Few-shot サンプル 2 件 + 手計算ケース 5 件 (合計 7 件)。
    # 実行コマンド: python -m core.revenue
    import json
    from pathlib import Path
    from pprint import pprint

    from core.phase import judge_phase

    examples_dir = Path(__file__).parent.parent / "prompts" / "examples"
    DEAL = 10_000_000  # 1,000 万円 (基準値)

    print("Phase 7 calc_expected_revenue 動作確認")
    print("=" * 70)

    # ─── ケース 1-2: 既存 Few-shot サンプル ────────────────────────────
    sample_cases = [
        ("sample_extraction_phase2.json", 2, 0.075, 750_000),
        ("sample_extraction_phase4.json", 4, 0.42875, 4_287_500),
    ]
    for filename, expected_phase, expected_prob, expected_rev in sample_cases:
        data = json.loads((examples_dir / filename).read_text(encoding="utf-8"))
        flat = {k: v["status"] for k, v in data.items()}
        phase = judge_phase(flat)
        result = calc_expected_revenue(DEAL, phase, flat)
        ok_phase = phase == expected_phase
        ok_prob = abs(result["probability"] - expected_prob) < 1e-9
        ok_rev = result["expected_revenue"] == expected_rev
        verdict = "OK" if (ok_phase and ok_prob and ok_rev) else "NG"
        print(f"\n[{verdict}] ケース: {filename} (deal={DEAL:,} 円)")
        print(
            f"   Phase: {phase} (期待 {expected_phase})  "
            f"prob: {result['probability']:.5f} (期待 {expected_prob:.5f})  "
            f"expected: {result['expected_revenue']:,} 円 (期待 {expected_rev:,} 円)"
        )
        pprint(result, sort_dicts=False, width=80, indent=2)

    # ─── ケース 3: Phase 0 (全項目 unconfirmed) ─────────────────────────
    all_unconfirmed = {k: "unconfirmed" for k in MEDDPICC_KEYS}
    phase = judge_phase(all_unconfirmed)
    result = calc_expected_revenue(DEAL, phase, all_unconfirmed)
    ok = phase == 0 and result["expected_revenue"] == 0
    print(f"\n[{'OK' if ok else 'NG'}] ケース 3: 全項目 unconfirmed")
    print(
        f"   Phase: {phase} (期待 0)  "
        f"expected: {result['expected_revenue']:,} 円 (期待 0)"
    )

    # ─── ケース 4: Phase 5 (全項目 confirmed) ─ 最大確率 0.9 ─────────────
    all_confirmed = {k: "confirmed" for k in MEDDPICC_KEYS}
    phase = judge_phase(all_confirmed)
    result = calc_expected_revenue(DEAL, phase, all_confirmed)
    # 手計算: 0.9 × 8/8 × 1.0 = 0.9 → 9,000,000
    ok = (
        phase == 5
        and abs(result["probability"] - 0.9) < 1e-9
        and result["expected_revenue"] == 9_000_000
    )
    print(f"\n[{'OK' if ok else 'NG'}] ケース 4: 全項目 confirmed (Phase 5 最大確率)")
    print(
        f"   Phase: {phase} (期待 5)  prob: {result['probability']:.5f} "
        f"(期待 0.90000)  expected: {result['expected_revenue']:,} 円 (期待 9,000,000)"
    )

    # ─── ケース 5: Phase 4 + risk 1 件 (Phase 4 サンプルと同じ条件) ──────
    # ケース 2 で検証済みだが、手計算式の独立確認として再掲。
    # 手計算: 0.7 × 7/8 × 0.7 = 0.42875 → 4,287,500
    # (ケース 2 と同値だが、JSON 由来ではなく辞書直書きでの再確認)
    risk1_case = {
        "metrics": "confirmed",
        "economic_buyer": "confirmed",
        "decision_criteria": "confirmed",
        "decision_process": "confirmed",
        "paper_process": "confirmed",
        "identify_pain": "confirmed",
        "champion": "confirmed",
        "competition": "risk",
    }
    phase = judge_phase(risk1_case)
    result = calc_expected_revenue(DEAL, phase, risk1_case)
    ok = phase == 4 and result["expected_revenue"] == 4_287_500
    print(f"\n[{'OK' if ok else 'NG'}] ケース 5: Phase 4 + risk 1 件 (手書き)")
    print(
        f"   Phase: {phase} (期待 4)  prob: {result['probability']:.5f} "
        f"(期待 0.42875)  expected: {result['expected_revenue']:,} 円 (期待 4,287,500)"
    )

    # ─── ケース 6: risk 2 件で減衰 0.49 倍を確認 ──────────────────────
    risk2_case = dict(risk1_case)
    risk2_case["paper_process"] = "risk"
    risk2_case["competition"] = "risk"
    # 手計算: Phase = ? identify_pain confirmed, champion confirmed,
    # economic_buyer confirmed, metrics confirmed → Phase 3 進む
    # → decision_criteria confirmed, decision_process confirmed,
    #   paper_process risk → Phase 3 で止まる
    # confirmed=6, risk=2 → 0.5 × 6/8 × 0.7^2 = 0.5 × 0.75 × 0.49 = 0.18375
    # → 1,837,500
    phase = judge_phase(risk2_case)
    result = calc_expected_revenue(DEAL, phase, risk2_case)
    ok = (
        phase == 3
        and abs(result["probability"] - 0.18375) < 1e-9
        and result["expected_revenue"] == 1_837_500
    )
    print(f"\n[{'OK' if ok else 'NG'}] ケース 6: Phase 3 + risk 2 件")
    print(
        f"   Phase: {phase} (期待 3)  prob: {result['probability']:.5f} "
        f"(期待 0.18375)  expected: {result['expected_revenue']:,} 円 (期待 1,837,500)"
    )

    # ─── ケース 7: deal_amount = 0 → 期待収益 0 ──────────────────────
    result = calc_expected_revenue(0, 5, all_confirmed)
    ok = result["expected_revenue"] == 0
    print(f"\n[{'OK' if ok else 'NG'}] ケース 7: deal_amount=0, Phase 5")
    print(f"   expected: {result['expected_revenue']:,} 円 (期待 0)")

    # ─── ケース 8: バリデーション (deal_amount 負値で ValueError) ──────
    print("\n[実行] ケース 8: deal_amount=-100 で ValueError 期待")
    try:
        calc_expected_revenue(-100, 1, all_unconfirmed)
        print("   NG: 例外が発生しなかった")
    except ValueError as e:
        print(f"   OK: ValueError 発生: {e}")

    print("\n" + "=" * 70)
    print("動作確認終わり")
