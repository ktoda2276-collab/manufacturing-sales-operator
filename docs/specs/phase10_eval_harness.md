# Phase 10 SPEC: 評価フレームワーク (Eval Harness ①)

## §1 目的とスコープ

### 目的

MEDDPICC 抽出 (`core/extractor.extract_meddpicc` / `core/pipeline.run_pipeline`)
の出力を、人手で正解付けした「ゴールドペア (議事録 + 期待 MEDDPICC 出力)」と
突き合わせ、抽出精度を**数値で**測る基盤を作る。

本 Phase の核心は **「箱」(評価機構) と「中身」(データセット件数) の分離**。
30 件のゴールドが揃わないと評価が始められない、という思い込みを壊す。既存の
Phase 2 / Phase 4 ゴールド 2 件だけで「箱」を組み、件数は後からインクリメンタルに
育てる (2 → 5 → … → 30)。

### このセッション (①) でやること

- `evals/metrics.py`: 純 Python・決定論的なメトリクス関数 (API 不使用)
- `evals/dataset/cases.json`: ケース台帳 (既存ゴールドを**参照**、複製しない)
- `evals/test_meddpicc.py`: `pytest` でメトリクス関数を**合成フィクスチャ**で検証
  (LLM を呼ばない = 無料・高速・決定論的)
- `evals/run_eval.py`: 実 API を叩いて精度を測る**実評価ランナー** (通常 `pytest`
  からは分離。明示実行時のみ課金)

### このセッションでやらないこと

- 30 件のゴールドデータ生成 (中身。クリティカルパス外でインクリメンタルに)
- Streamlit UI (Phase 11)
- ゴールド JSON → DB 投入スクリプト (DB シード兼用は中身を増やす段で設計)
- プロンプト改善・モデル比較 (実評価で数値が出てから着手)

---

## §2 設計判断

### 軸 1 (可逆 vs 不可逆): 既存ゴールドは複製せず参照する

`prompts/examples/sample_extraction_phase{2,4}.json` を `evals/dataset/` に
コピーすると二重管理になり、片方を直すともう片方がドリフトする (不可逆的に
不整合化しうる)。台帳 `cases.json` に**パス参照**だけ持たせ、ゴールドの唯一の
ソースは `prompts/examples/` 側に保つ。

### 軸 2 (判断は LLM、計算は Python): メトリクスは完全に純 Python

status 判定と evidence 抽出は LLM の責務 (`extractor.py`)。一致率の計算・分類は
決定論的な Python の責務 (`metrics.py`)。この分離により、メトリクス関数は API を
呼ばずにフィクスチャで単体検証でき、`pytest` が無料・高速・再現的になる。

### 軸 3 (Defense in Depth): 「箱」と「中身」と「課金」の3分離

- **箱の検証** (`test_meddpicc.py`): 合成した predicted dict を metrics に渡し、
  期待スコアが出るかを確認。件数ゼロ・コストゼロで機構の正しさを担保。
- **中身の検証** (`run_eval.py`): 実 API 出力 vs ゴールドの実精度測定。課金あり。
- 通常 `pytest` は箱だけ回す。中身 (実評価) は人間が明示的に走らせる。

LLM の確率的挙動を**毎回の CI/テストに混ぜない**ことで、テストの flaky 化と
意図しない API 課金を二重に防ぐ。

---

## §3 メトリクス仕様

ステータスは 4 値: `confirmed` / `partial` / `unconfirmed` / `risk`
(Phase 2 ゴールドに confirmed/partial/unconfirmed、Phase 4 ゴールドに
confirmed/risk が現れ、2 件で全 4 値を被覆)。

MEDDPICC 8 項目: `metrics` / `economic_buyer` / `decision_criteria` /
`decision_process` / `paper_process` / `identify_pain` / `champion` /
`competition`。

### §3.1 status 厳密一致 (`status_match`)

8 項目それぞれで `predicted[item]["status"] == gold[item]["status"]` を判定し、
一致数 / 8 を `strict_rate` として返す。項目別の真偽内訳も返す
(不一致箇所の特定のため)。

### §3.2 境界分析 (`boundary_analysis`) — 厳密率 + 許容率の2値

Day 7 で `decision_criteria` が「期待 unconfirmed → 実出力 partial」という
**LLM 判定揺れ**を起こした。partial と unconfirmed の境界は本質的に曖昧で、
これは抽出の致命的失敗ではない。一方 `confirmed` と `unconfirmed` の取り違えや
`risk` 絡みの誤りは重大 (フェーズ判定・期待収益に直結する)。

そこで不一致を 2 種に分類する:

- **境界揺れ (boundary)**: `{partial, unconfirmed}` 間の swap。許容ノイズ。
- **重大誤り (serious)**: それ以外の不一致 (confirmed/risk が絡む、2 段跳びなど)。

返す指標:

- `strict_rate` = 完全一致数 / 8
- `tolerant_rate` = (完全一致数 + 境界揺れ数) / 8
- `boundary_mismatches` / `serious_mismatches`: それぞれの項目リスト

`strict_rate` と `tolerant_rate` の差が「境界揺れにどれだけ精度を食われているか」を
そのまま表す。面接ナラティブ: **「許容できる判定揺れと、フェーズを誤らせる重大な
抽出ミスを区別して測る」**。

---

## §4 ファイル構成

```
evals/
├── __init__.py
├── dataset/
│   └── cases.json        # [{name, transcript_path, gold_path, expected_phase}]
├── metrics.py            # status_match / boundary_analysis (純Python)
├── test_meddpicc.py      # pytest: metrics を合成フィクスチャで検証 (APIなし)
└── run_eval.py           # 実API評価 (明示実行のみ、通常pytestから分離)
```

`cases.json` を 2 → 30 件に増やすだけで「中身」が育つ。`metrics.py` /
`test_meddpicc.py` (箱) は件数に依存しない。

---

## §5 受け入れ基準

- `pytest evals/` が全グリーン (API を一切呼ばない)
- `status_match`: 完全一致で strict_rate=1.0、1 項目ずらすと 7/8
- `boundary_analysis`:
  - partial↔unconfirmed の swap 1 件で strict 7/8・tolerant 8/8・boundary 1 件
  - confirmed→unconfirmed の swap 1 件で strict 7/8・tolerant 7/8・serious 1 件
- `cases.json` の各ケースのゴールドが実在し、8 項目・有効ステータスを持つ
- `run_eval.py` は import で API を呼ばない (実行時のみ叩く)

---

*Created: 2026-06-04 (Day 11 / Phase 10 ①)*
