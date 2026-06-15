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

# -----------------------------------------------------------------------------
# フェーズ要件テーブル (単一ソース、軸 3)
# -----------------------------------------------------------------------------
# 「そのフェーズに到達するために、新たに confirmed が必要な項目」をフェーズ昇順で
# 並べる。判定は累積 AND: 上から順に全項目が confirmed なら次のフェーズへ進む。
# 途中で 1 項目でも未 confirmed なら、そこで到達フェーズが確定する。
#
# judge_phase() (フェーズ判定) と analyze_gaps() (不足項目算出) の両方がこの
# テーブルを唯一の参照元にする。片方だけ直して不整合になる事故を防ぐ (DRY)。
# 並び順は docs/specs/phase7_phase_revenue.md の「フェーズ別必須項目」と一致。
PHASE_REQUIREMENTS: tuple[tuple[int, tuple[str, ...]], ...] = (
    (1, ("identify_pain",)),
    (2, ("champion",)),
    (3, ("economic_buyer", "metrics")),
    (4, ("decision_criteria", "decision_process", "paper_process")),
    (5, ("competition",)),
)

# MEDDPICC 8 項目の日本語ラベル (UI 表示用)。キーは MEDDPICC_KEYS と一致。
ITEM_LABELS: dict[str, str] = {
    "metrics": "Metrics（指標・効果）",
    "economic_buyer": "Economic Buyer（決裁者）",
    "decision_criteria": "Decision Criteria（評価基準）",
    "decision_process": "Decision Process（意思決定プロセス）",
    "paper_process": "Paper Process（契約・稟議プロセス）",
    "identify_pain": "Identify Pain（課題）",
    "champion": "Champion（推進者）",
    "competition": "Competition（競合）",
}

# 不足項目ごとの「次アクション」提案文 (UI 表示用)。analyze_gaps が付与する。
# 「何を確定させれば次フェーズに進めるか」を営業の行動レベルで言語化する。
NEXT_ACTION_HINTS: dict[str, str] = {
    "identify_pain": "顧客の課題（Pain）をヒアリングで具体化し、合意を取る",
    "champion": "社内で案件を推進してくれるキーパーソン（Champion）を特定し関係を強化する",
    "economic_buyer": "予算権限を持つ決裁者（Economic Buyer）にアクセスし合意を得る",
    "metrics": "導入効果を定量指標（Metrics）として顧客と握る",
    "decision_criteria": "顧客の評価基準（Decision Criteria）を引き出し自社優位に整える",
    "decision_process": "意思決定の関与者と進め方（Decision Process）を確認する",
    "paper_process": "契約・稟議・調達の手続き（Paper Process）と所要期間を把握する",
    "competition": "競合状況（Competition）を把握し差別化ポイントを固める",
}


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

    # PHASE_REQUIREMENTS を昇順に走査し、累積 AND で到達フェーズを求める。
    # あるフェーズの必須項目が 1 つでも未 confirmed なら、その手前で確定して打ち切る。
    # (旧実装の if 連鎖と同一の挙動。要件をテーブルに外出ししただけ。)
    phase = 0
    for ph, required_keys in PHASE_REQUIREMENTS:
        if all(_is_confirmed(key) for key in required_keys):
            phase = ph
        else:
            break
    return phase


def analyze_gaps(meddpicc_evaluations: dict[str, str]) -> dict:
    """現在フェーズと、次フェーズ到達に不足している項目・リスク項目・次アクションを返す。

    CLAUDE.md §7 の v1 要件「不足項目のハイライト + 次アクション提案」を満たす
    純 Python 関数。LLM は使わない (軸 2)。判定の参照元は judge_phase と同じ
    PHASE_REQUIREMENTS (軸 3、DRY)。

    入力検証 (キー・status enum) と現在フェーズ算出は judge_phase に委譲する
    (二重実装しない)。judge_phase が ValueError を投げればそのまま透過する。

    Args:
        meddpicc_evaluations: MEDDPICC 8 項目の status を持つ flat dict。
            judge_phase と同じ入力契約。

    Returns:
        分析結果 dict (構造は docs/specs/phase11_streamlit_ui.md §3.1 参照):
            {
              "current_phase": int,         # 0〜5
              "next_phase": int | None,     # 5 到達時は None
              "missing_items": [            # 次フェーズ必須かつ未 confirmed の項目
                  {"key": str, "label": str, "status": str, "action": str}, ...
              ],
              "risk_items": [              # status == "risk" の項目 (フェーズ非依存)
                  {"key": str, "label": str}, ...
              ],
            }

    Raises:
        ValueError: judge_phase と同じ条件 (キー不一致 / status enum 違反) で透過。
    """
    # judge_phase が検証 + 現在フェーズ算出を兼ねる。ここで不正入力は弾かれる。
    current_phase = judge_phase(meddpicc_evaluations)

    # status == "risk" の項目はフェーズ前進と独立に「危険信号」として常に拾う。
    # ITEM_LABELS の定義順 (MEDDPICC 正準順) で並べ、表示順を安定させる。
    risk_items = [
        {"key": key, "label": ITEM_LABELS[key]}
        for key in ITEM_LABELS
        if meddpicc_evaluations[key] == "risk"
    ]

    # 最終フェーズ到達済みなら「次フェーズ」も「不足項目」も無い (リスクのみ返す)。
    if current_phase >= 5:
        return {
            "current_phase": current_phase,
            "next_phase": None,
            "missing_items": [],
            "risk_items": risk_items,
        }

    # 次フェーズの必須項目のうち、まだ confirmed でないものが「不足項目」。
    # PHASE_REQUIREMENTS は (phase, keys) のタプル列なので dict 化して引く。
    next_phase = current_phase + 1
    requirements = dict(PHASE_REQUIREMENTS)
    next_required_keys = requirements[next_phase]
    missing_items = [
        {
            "key": key,
            "label": ITEM_LABELS[key],
            "status": meddpicc_evaluations[key],
            "action": NEXT_ACTION_HINTS[key],
        }
        for key in next_required_keys
        if meddpicc_evaluations[key] != "confirmed"
    ]

    return {
        "current_phase": current_phase,
        "next_phase": next_phase,
        "missing_items": missing_items,
        "risk_items": risk_items,
    }


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
