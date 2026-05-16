# Phase 7 SPEC: 商談フェーズ判定 + 期待収益計算

## 目的

Phase 6 (`core/extractor.py`) で抽出した MEDDPICC 評価から、商談の現在フェーズ
(Phase 0〜5)と期待収益を機械的に算出する。

## 設計判断軸との整合

- **軸 1 (可逆 vs 不可逆)**: v1 では固定確率(可逆)。v3 で動的確率に置換する前提。
- **軸 2 (判断は LLM、計算は Python)**: Phase 7 は純粋 Python、LLM 不使用。
- **軸 3 (Defense in Depth)**: フェーズ判定と期待収益を別関数 (別モジュール)
  に分離し、独立してテスト・差し替え可能にする。

## 入力データ

MEDDPICC 評価結果: `dict[str, str]` (フラットな status マップ)

```
{
  "metrics":           "confirmed" | "partial" | "unconfirmed" | "risk",
  "economic_buyer":    ...,
  "decision_criteria": ...,
  "decision_process":  ...,
  "paper_process":     ...,
  "identify_pain":     ...,
  "champion":          ...,
  "competition":       ...
}
```

注: `core/extractor.py` の返却値は `{"status": ..., "evidence": ...}` の
ネスト構造なので、呼び出し側で
`{k: v["status"] for k, v in extracted.items()}` で flatten してから本関数に渡す。
Phase 7 関数の入力契約は flat dict に統一する (テストとシリアライズが楽)。

## フェーズ判定ロジック (`core/phase.py`)

### 判定規則

- **confirmed のみカウント** (`partial` / `unconfirmed` / `risk` は未到達扱い)
- **全条件 AND**(累積的)
- `identify_pain` が confirmed でない場合は **Phase 0**

| Phase | 累積必須項目 (前 Phase の全条件 + 以下) |
|---|---|
| 1 | `identify_pain` |
| 2 | + `champion` |
| 3 | + `economic_buyer`, `metrics` |
| 4 | + `decision_criteria`, `decision_process`, `paper_process` |
| 5 | + `competition` |

(Phase 5 = 8 項目すべてが confirmed)

### 設計意図(BtoB 営業の典型的進行)

| Phase | 意味 |
|---|---|
| 0 | MEDDPICC 情報が不十分 (議事録に痛みの言及がない) |
| 1 | 顧客が痛みを認めた (Pain confirmed) |
| 2 | 社内に味方が育った (Champion confirmed) |
| 3 | 決裁者と ROI が見えた (EB + Metrics confirmed) |
| 4 | 意思決定プロセス・契約パスが明確化 (DC + DP + PP confirmed) |
| 5 | 競合環境を把握し、勝てる見立てが立った (Competition confirmed) |

EB を Phase 2 ではなく Phase 3 に置いた理由: 実務では Champion を介して EB の所在を
特定するため、Champion → EB の順が自然。Competition を Phase 5 に置いた理由: MEDDPICC
文献では「常時意識」項目だが、フェーズモデルとしては「最終判断前の競合整理」として
末端に置くのが現実的。

### 関数シグネチャ

```python
def judge_phase(meddpicc_evaluations: dict[str, str]) -> int:
    """商談フェーズ (0〜5) を返す。"""
```

## 期待収益計算 (`core/revenue.py`)

### 計算式

```
期待収益    = deal_amount × 受注確率
受注確率   = フェーズ基本確率 × MEDDPICC 補正 × risk 減衰
フェーズ基本確率 = {0: 0.0, 1: 0.1, 2: 0.3, 3: 0.5, 4: 0.7, 5: 0.9}
MEDDPICC 補正   = confirmed の数 / 8
risk 減衰      = 0.7 ^ (risk の数)
```

### 関数シグネチャ

```python
def calc_expected_revenue(
    deal_amount: int,
    phase: int,
    meddpicc_evaluations: dict[str, str],
) -> dict:
```

### 返却 dict 構造

```
{
  "deal_amount":       int,            # 入力をそのまま反映
  "phase":             int,            # 入力をそのまま反映
  "probability":       float,          # 0.0 〜 0.9
  "expected_revenue":  int,            # round(deal_amount * probability)
  "factors": {
    "phase_base":         float,
    "meddpicc_correction": float,
    "risk_decay":          float,
    "confirmed_count":     int,
    "risk_count":          int,
  }
}
```

`factors` を保持する理由: Phase 8 以降のレポート画面で「なぜこの期待収益か」を
分解表示するため。計算の説明可能性を確保する。

## エッジケース

| ケース | 挙動 |
|---|---|
| `identify_pain` が confirmed でない | Phase 0 (議事録に MEDDPICC 情報が不十分な状態) |
| 入力 dict のキーが 8 項目と一致しない | `ValueError` |
| 入力 dict の値が 4 enum 外 | `ValueError` |
| `deal_amount` が負 | `ValueError` |
| `phase` が 0〜5 外 | `ValueError` |
| `deal_amount` = 0 | 期待収益 = 0 (確率に関わらず) |

## 受け入れ基準

- `sample_extraction_phase2.json` の status を flatten した dict で
  `judge_phase` → **2** を返す。
- `sample_extraction_phase4.json` の status を flatten した dict で
  `judge_phase` → **4** を返す。
- 期待収益計算: 手計算と一致 (テストケース 5 件以上を `__main__` で実行確認)。

## 既存サンプルでの想定値

### Phase 2 サンプル (`sample_extraction_phase2.json`)

- confirmed: `identify_pain`, `champion` (2 件)
- partial: `metrics` (1 件)
- unconfirmed: 残り 5 件
- → **Phase 2**, probability = 0.3 × 2/8 × 1.0 = 0.075
- deal=10,000,000 → expected_revenue = **750,000**

### Phase 4 サンプル (`sample_extraction_phase4.json`)

- confirmed: 7 件 (`competition` を除く)
- risk: `competition` (1 件)
- → **Phase 4**, probability = 0.7 × 7/8 × 0.7 = 0.42875
- deal=10,000,000 → expected_revenue = **4,287,500**
