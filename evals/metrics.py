"""MEDDPICC 抽出精度のメトリクス (Phase 10 ①)

設計判断 (docs/specs/phase10_eval_harness.md):
- 軸 2 (判断は LLM、計算は Python): status 判定は LLM (core/extractor.py) の責務。
  ここは「予測 dict と正解 dict を突き合わせて数値を出す」決定論的な計算層。
  API を一切呼ばないので、合成フィクスチャで単体検証でき pytest が無料・高速。
- 軸 3 (Defense in Depth): partial↔unconfirmed の「許容できる判定揺れ」と、
  confirmed/risk が絡む「フェーズを誤らせる重大誤り」を分けて測る。

入力 dict の構造 (SPEC.md §6 / core/extractor.py と同一):
    {
      "metrics": {"status": "partial", "evidence": {...}},
      "economic_buyer": {"status": "unconfirmed", "evidence": {...}},
      ... (MEDDPICC 8 項目)
    }
evidence の中身は精度計算に使わない (status のみ比較)。
"""

# MEDDPICC 8 項目の正準キー集合。予測・正解の双方がこの 8 項目を持つ前提。
MEDDPICC_ITEMS: tuple[str, ...] = (
    "metrics",
    "economic_buyer",
    "decision_criteria",
    "decision_process",
    "paper_process",
    "identify_pain",
    "champion",
    "competition",
)

# 有効なステータス 4 値。
VALID_STATUSES: frozenset[str] = frozenset(
    {"confirmed", "partial", "unconfirmed", "risk"}
)

# 「許容できる判定揺れ」とみなすステータスの組 (順不同)。
# partial と unconfirmed の境界は本質的に曖昧 (Day 7 の decision_criteria 事例)。
# ここに該当する不一致は致命的でないノイズとして tolerant_rate で救済する。
_BOUNDARY_PAIR: frozenset[str] = frozenset({"partial", "unconfirmed"})


def _status_of(record: dict, item: str) -> str:
    """8 項目の 1 つから status 文字列を取り出す。

    予測 dict にキー欠落や status 欠落があれば「抽出フォーマット違反」として
    例外にせず、有効値に存在しない番兵 "__missing__" を返す。これにより
    「不一致」として自然にスコアへ反映される (欠落 = 誤りの一種)。
    """
    body = record.get(item)
    if not isinstance(body, dict):
        return "__missing__"
    return body.get("status", "__missing__")


def status_match(predicted: dict, gold: dict) -> dict:
    """8 項目の status 厳密一致を測る (§3.1)。

    Args:
        predicted: 抽出器の出力 dict (8 項目フラット並列)。
        gold: 正解 dict (同構造)。

    Returns:
        {
          "strict_rate": float,        # 完全一致数 / 8
          "match_count": int,          # 完全一致数
          "total": int,                # 8 (固定)
          "per_item": {item: bool},    # 項目別の一致真偽
        }
    """
    per_item: dict[str, bool] = {}
    for item in MEDDPICC_ITEMS:
        per_item[item] = _status_of(predicted, item) == _status_of(gold, item)

    match_count = sum(per_item.values())
    total = len(MEDDPICC_ITEMS)
    return {
        "strict_rate": match_count / total,
        "match_count": match_count,
        "total": total,
        "per_item": per_item,
    }


def boundary_analysis(predicted: dict, gold: dict) -> dict:
    """不一致を「境界揺れ」と「重大誤り」に分類し、厳密率と許容率を返す (§3.2)。

    - 境界揺れ (boundary): {partial, unconfirmed} 間の swap。許容ノイズ。
    - 重大誤り (serious): それ以外の不一致 (confirmed/risk 絡み、2 段跳び等)。

    Args:
        predicted: 抽出器の出力 dict。
        gold: 正解 dict。

    Returns:
        {
          "strict_rate": float,          # 完全一致数 / 8
          "tolerant_rate": float,        # (完全一致 + 境界揺れ) / 8
          "boundary_mismatches": [       # partial↔unconfirmed の取り違え
              {"item": str, "predicted": str, "gold": str}, ...
          ],
          "serious_mismatches": [        # フェーズを誤らせうる重大な誤り
              {"item": str, "predicted": str, "gold": str}, ...
          ],
        }

    strict_rate と tolerant_rate の差が「境界揺れにどれだけ精度を食われたか」を
    そのまま表す。tolerant が高く strict が低いなら、抽出の骨格は合っており
    partial/unconfirmed の線引きだけが揺れている、と読める。
    """
    total = len(MEDDPICC_ITEMS)
    match_count = 0
    boundary: list[dict[str, str]] = []
    serious: list[dict[str, str]] = []

    for item in MEDDPICC_ITEMS:
        pred = _status_of(predicted, item)
        truth = _status_of(gold, item)

        if pred == truth:
            match_count += 1
            continue

        mismatch = {"item": item, "predicted": pred, "gold": truth}
        # 予測・正解の両方が {partial, unconfirmed} に収まる不一致だけを境界揺れ扱い。
        # 欠落 ("__missing__") は _BOUNDARY_PAIR に含まれないので必ず serious になる。
        if {pred, truth} == _BOUNDARY_PAIR:
            boundary.append(mismatch)
        else:
            serious.append(mismatch)

    return {
        "strict_rate": match_count / total,
        "tolerant_rate": (match_count + len(boundary)) / total,
        "boundary_mismatches": boundary,
        "serious_mismatches": serious,
    }
