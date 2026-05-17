"""統合フローモジュール (Phase 8)

Phase 5 (プロンプト) → Phase 6 (`core/extractor.py`) → Phase 7
(`core/phase.py` / `core/revenue.py`) の 3 層を一気通貫で呼び出す統合層。
v1 完成形における唯一のエントリポイントとして、議事録 (str) と商談金額 (int)
だけを受け取り、MEDDPICC 評価 / フェーズ / 期待収益を統合 dict で返す。

設計判断:
- 軸 1 (可逆 vs 不可逆): 戻り値は dict。dataclass 化は v3 で後付け可能なため、
  今は dict のままで済ませる (YAGNI)。
- 軸 2 (判断は LLM、計算は Python): pipeline は「調整役」のみで業務ロジックを
  持たない。LLM 判断は extractor、決定論的計算は phase / revenue に委譲し、
  pipeline 自身の責務は「順序制御」と「ネスト → flat の構造変換」だけ。
- 軸 3 (Defense in Depth): pipeline 自身は入力契約 (transcript / deal_amount)
  の最小検証のみ。3 モジュールが投げる例外は素通しする (try で握りつぶさない)。
  extractor 戻り値の MEDDPICC キー検証は phase.judge_phase() が行うため、
  pipeline では二重検証しない (DRY)。

参照:
- docs/specs/phase8_integrated_flow.md: 本モジュールの仕様
- core/extractor.py: MEDDPICC 抽出 (Phase 6, LLM 呼び出し)
- core/phase.py: フェーズ判定 (Phase 7, 決定論)
- core/revenue.py: 期待収益計算 (Phase 7, 決定論)
"""
from core.extractor import extract_meddpicc
from core.phase import judge_phase
from core.revenue import calc_expected_revenue


def run_pipeline(transcript: str, deal_amount: int) -> dict:
    """議事録と商談金額から MEDDPICC 評価・フェーズ・期待収益を一気通貫で算出する。

    内部処理フロー (詳細は docs/specs/phase8_integrated_flow.md §4):
        1. 入力バリデーション (transcript 空、deal_amount 負を弾く)
        2. extractor.extract_meddpicc(transcript) で MEDDPICC 抽出 (ネスト構造)
        3. ネスト → flat 変換 ({k: v["status"] for k, v in nested.items()})
        4. phase.judge_phase(flat) でフェーズ判定
        5. revenue.calc_expected_revenue(deal_amount, phase, flat) で期待収益
        6. 統合 dict を返却

    ネスト → flat 変換を pipeline 側で行うのは、core/phase.py /
    core/revenue.py の入力契約が flat dict に統一されている (Phase 7 SPEC) 一方、
    extractor 側はネストのまま保持したい (evidence の保持価値が高い) ため。
    両者の境界変換は調整役である pipeline が持つのが自然。

    Args:
        transcript: 商談議事録 (録音文字起こし)。空文字列・空白のみは不可。
            話者ラベルは「苗字 役職(顧客/自社):」形式を想定 (SPEC.md §7)。
        deal_amount: 商談金額 (円)。0 以上の int (0 は許容、期待収益も 0 になる)。

    Returns:
        統合結果 dict:
            {
              "meddpicc_evaluations": dict,  # extractor 戻り値 (ネスト、加工なし)
              "phase": int,                   # 0〜5
              "revenue": dict,                # calc_expected_revenue 戻り値
            }

    Raises:
        ValueError: 入力契約違反 (transcript 空、deal_amount 負)。
            あるいは下位層 (phase / revenue) の検証エラー (MEDDPICC キー不一致
            など) が透過した場合。
        RuntimeError: ANTHROPIC_API_KEY 未設定 (extractor から透過)。
        anthropic.APIError 系: API 呼び出しエラー (extractor から透過、
            Phase 10 で堅牢化)。
        json.JSONDecodeError: extractor の応答 JSON パース失敗 (透過)。
    """
    # 入力契約の最小検証のみ実施。MEDDPICC キーや status enum の検証は下位層
    # (phase.judge_phase) が行うため、ここでは触らない (DRY、軸 3)。
    if not transcript or not transcript.strip():
        raise ValueError("transcript は空文字列不可 (空白のみも不可)")
    if deal_amount < 0:
        raise ValueError(f"deal_amount は 0 以上必須: {deal_amount}")

    # Step 2: MEDDPICC 抽出 (LLM 判断)。API 例外・JSON パース失敗は透過。
    nested = extract_meddpicc(transcript)

    # Step 3: ネスト → flat 変換 (pipeline の責務)。
    # core/phase.py / core/revenue.py は flat dict[str, str] を要求する。
    flat = {k: v["status"] for k, v in nested.items()}

    # Step 4: フェーズ判定 (決定論)。キー不一致や status enum 違反はここで弾かれる。
    phase = judge_phase(flat)

    # Step 5: 期待収益計算 (決定論)。
    revenue = calc_expected_revenue(deal_amount, phase, flat)

    # Step 6: 統合 dict を返却。meddpicc_evaluations はネストのまま返し、
    # 後段 (UI / DB) で evidence にアクセスできる状態を保つ。
    return {
        "meddpicc_evaluations": nested,
        "phase": phase,
        "revenue": revenue,
    }


if __name__ == "__main__":
    # 動作確認: Phase 2 / Phase 4 サンプル議事録での正常系 2 ケース +
    # 入力契約違反の異常系 2 ケース (計 4 ケース)。
    # 実行コマンド: python -m core.pipeline
    # 注意: 正常系は API を実呼び出しするため約 $0.20 のコストが発生する。
    from pathlib import Path
    from pprint import pprint

    examples_dir = Path(__file__).parent.parent / "prompts" / "examples"
    DEAL = 10_000_000  # 1,000 万円 (基準値)

    print("Phase 8 run_pipeline 動作確認")
    print("=" * 70)

    # ─── ケース 1: Phase 2 サンプル議事録 ───────────────────────────────
    sample_path_p2 = examples_dir / "sample_transcript_phase2.md"
    transcript_p2 = sample_path_p2.read_text(encoding="utf-8")
    print(f"\n[ケース 1] {sample_path_p2.name} (deal={DEAL:,} 円)")
    print(f"  入力: {len(transcript_p2):,} 文字")
    print("  期待: phase=2 (LLM 揺らぎで ±1 は許容)")
    print("  --- 実行 ---")
    result_p2 = run_pipeline(transcript_p2, DEAL)
    print(f"  phase: {result_p2['phase']}")
    print(f"  revenue:")
    pprint(result_p2["revenue"], sort_dicts=False, width=80, indent=4)

    # ─── ケース 2: Phase 4 サンプル議事録 ───────────────────────────────
    sample_path_p4 = examples_dir / "sample_transcript_phase4.md"
    transcript_p4 = sample_path_p4.read_text(encoding="utf-8")
    print(f"\n[ケース 2] {sample_path_p4.name} (deal={DEAL:,} 円)")
    print(f"  入力: {len(transcript_p4):,} 文字")
    print("  期待: phase=4 (LLM 揺らぎで ±1 は許容)")
    print("  --- 実行 ---")
    result_p4 = run_pipeline(transcript_p4, DEAL)
    print(f"  phase: {result_p4['phase']}")
    print(f"  revenue:")
    pprint(result_p4["revenue"], sort_dicts=False, width=80, indent=4)

    # ─── ケース 3 (異常系): transcript="" で ValueError 期待 ────────────
    print("\n[ケース 3] transcript='' で ValueError 期待 (API は叩かない)")
    try:
        run_pipeline("", DEAL)
        print("  NG: 例外が発生しなかった")
    except ValueError as e:
        print(f"  OK: ValueError 発生: {e}")

    # ─── ケース 4 (異常系): deal_amount=-1 で ValueError 期待 ────────────
    print("\n[ケース 4] deal_amount=-1 で ValueError 期待 (API は叩かない)")
    try:
        run_pipeline("有効な議事録テキスト", -1)
        print("  NG: 例外が発生しなかった")
    except ValueError as e:
        print(f"  OK: ValueError 発生: {e}")

    print("\n" + "=" * 70)
    print("動作確認終わり")
