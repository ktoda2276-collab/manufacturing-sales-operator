"""実評価ランナー (Phase 10 ①) — 「中身」の検証

通常の `pytest` から分離された、実 API を叩く評価。ケース台帳の各議事録を
core/pipeline.run_pipeline() に通し、出力 MEDDPICC を正解 (ゴールド) と
突き合わせて精度を集計・表示する。

設計判断 (docs/specs/phase10_eval_harness.md §2):
- 軸 3 (3 分離): LLM の確率的挙動と API 課金を毎回のテストに混ぜない。
  精度測定が必要なときだけ人間が明示的に走らせる。
- import では API を呼ばない (モジュールロードのみ)。実呼び出しは run_eval() 内。

実行方法:
    cd <project root>
    source venv/bin/activate
    python -m evals.run_eval          # 全ケースを実 API で評価 (課金あり)

コスト目安: 1 ケースあたり Phase 2/4 級 (~20K 字) で約 $0.10。
"""
from evals import load_cases, load_gold, load_transcript
from evals.metrics import boundary_analysis, status_match

# フェーズ・期待収益は精度測定の対象外だが、run_pipeline が deal_amount>=0 を
# 要求するためダミー金額を渡す。phase は deal_amount に依存しない。
_DUMMY_DEAL_AMOUNT = 1_000_000


def evaluate_case(case: dict) -> dict:
    """1 ケースを実 API で評価し、メトリクスをまとめて返す。

    遅延 import: run_pipeline (= anthropic 依存) はこの関数内で読む。
    モジュール import 段階で API 周りを引き込まないため。
    """
    from core.pipeline import run_pipeline

    transcript = load_transcript(case)
    gold = load_gold(case)

    result = run_pipeline(transcript, _DUMMY_DEAL_AMOUNT)
    # meddpicc_evaluations はネスト (status+evidence)。ゴールドと同形なので直接比較可。
    predicted = result["meddpicc_evaluations"]

    match = status_match(predicted, gold)
    boundary = boundary_analysis(predicted, gold)

    return {
        "name": case["name"],
        "strict_rate": match["strict_rate"],
        "tolerant_rate": boundary["tolerant_rate"],
        "boundary_mismatches": boundary["boundary_mismatches"],
        "serious_mismatches": boundary["serious_mismatches"],
        "expected_phase": case.get("expected_phase"),
        "actual_phase": result["phase"],
    }


def run_eval() -> list[dict]:
    """全ケースを評価し、結果リストを返す (集計表示は呼び出し側)。"""
    return [evaluate_case(case) for case in load_cases()]


def _print_report(results: list[dict]) -> None:
    """評価結果を人間可読のレポートとして標準出力に書く。"""
    print("=" * 60)
    print("MEDDPICC 抽出 実評価レポート")
    print("=" * 60)

    for r in results:
        phase_ok = "OK" if r["expected_phase"] == r["actual_phase"] else "NG"
        print(f"\n[{r['name']}]")
        print(f"  strict_rate   : {r['strict_rate']:.3f}")
        print(f"  tolerant_rate : {r['tolerant_rate']:.3f}")
        print(
            f"  phase         : 期待 {r['expected_phase']} / 実 "
            f"{r['actual_phase']} ({phase_ok})"
        )
        if r["boundary_mismatches"]:
            print("  境界揺れ (許容):")
            for m in r["boundary_mismatches"]:
                print(f"    - {m['item']}: 実 {m['predicted']} / 正 {m['gold']}")
        if r["serious_mismatches"]:
            print("  重大誤り:")
            for m in r["serious_mismatches"]:
                print(f"    - {m['item']}: 実 {m['predicted']} / 正 {m['gold']}")

    n = len(results)
    if n:
        mean_strict = sum(r["strict_rate"] for r in results) / n
        mean_tolerant = sum(r["tolerant_rate"] for r in results) / n
        print("\n" + "-" * 60)
        print(f"全 {n} ケース平均: strict {mean_strict:.3f} / "
              f"tolerant {mean_tolerant:.3f}")


if __name__ == "__main__":
    _print_report(run_eval())
