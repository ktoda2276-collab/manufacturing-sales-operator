# Phase 11 SPEC: Streamlit UI（読み取り専用 + 不足項目ハイライト）

## §1 目的とスコープ

### 目的

これまで `python -m ...` の CLI でしか確認できなかった MEDDPICC 評価結果を、
ブラウザで閲覧できる最小 UI にする。CLAUDE.md §7 の v1 要件のうち以下を満たす:

- 商談一覧ビュー（金額・確率・期待収益でソート可能）
- 不足項目のハイライト + 次アクション提案

### このセッションでやること

- `core/phase.py` に **`analyze_gaps()`**（純 Python）を追加。現在フェーズと、
  次フェーズ到達に不足している項目・リスク項目・次アクションを返す。
  併せて既存 `judge_phase()` をフェーズ要件テーブル `PHASE_REQUIREMENTS` 駆動に
  リファクタ（**挙動は不変**、DRY で要件を単一ソース化）。
- `app.py`（Streamlit エントリポイント）: **読み取り専用**の一覧ビュー + 詳細ビュー。
  `MSORepository` 経由でのみ DB に触れる。

### このセッションでやらないこと

- **新規評価フォーム（議事録 → `run_pipeline` → 保存）**。LLM 課金が発生するため
  本 Phase のスコープ外（読み取り専用に限定）。次 Phase 以降で検討。
- 認証・マルチユーザー・デプロイ。v1 はローカル単一ユーザー前提（CLAUDE.md §7）。
- 30 件ゴールド生成（クリティカルパス外、別途インクリメンタルに）。

---

## §2 設計判断

### 軸 1（可逆 vs 不可逆）: UI 層は「表示のみ」、データを書き換えない

読み取り専用にすることで、UI のバグが永続データを壊すリスクをゼロにする。
新規評価（書き込み・課金あり）は後続 Phase に分離し、まず「見える化」を可逆な
範囲で確立する。

### 軸 2（判断は LLM、計算は Python）: UI はロジックを持たない

フェーズ判定・期待収益・不足項目分析はすべて `core/` の純関数が算出する。
`app.py` は「`MSORepository` から取得 → Streamlit ウィジェットに流す」だけで、
業務ロジックを再実装しない。不足項目分析も `core/phase.analyze_gaps()` に置き、
UI からは呼ぶだけにする（テスト可能性を UI の外に確保）。

### 軸 3（Defense in Depth）: フェーズ要件の単一ソース化

`judge_phase()` に直書きされていたフェーズ別必須項目を `PHASE_REQUIREMENTS`
（データ）に集約し、`judge_phase()` と `analyze_gaps()` の両方がそれを参照する。
「フェーズ判定」と「不足項目算出」が同じ要件定義を見るので、片方だけ直して
不整合になる事故を防ぐ。

### DB アクセス方針

`app.py` は再描画のたびに `with MSORepository(DB_PATH) as repo:` で短命接続を開く。
SQLite のローカル接続は安価で、Streamlit の再実行（スレッド）と sqlite3 接続の
相性問題（`check_same_thread`）を接続を持ち越さないことで回避する。DB が無ければ
`MSORepository` がスキーマ適用して空 DB を作るため、初回でもクラッシュしない
（空 → 「データ未投入」案内を表示）。

---

## §3 機能仕様

### §3.1 `core/phase.analyze_gaps(meddpicc_evaluations) -> dict`

入力は `judge_phase()` と同じ flat dict（8 項目 / 4 値）。内部で `judge_phase()`
を呼んでキー・値の妥当性検証と現在フェーズ算出を兼ねる（DRY）。

返り値:

```python
{
  "current_phase": int,          # 0〜5
  "next_phase": int | None,      # 5 到達時は None
  "missing_items": [             # 次フェーズ必須かつ未 confirmed の項目
      {"key": str, "label": str, "status": str, "action": str}, ...
  ],
  "risk_items": [                # status == "risk" の項目（フェーズ非依存で警告）
      {"key": str, "label": str}, ...
  ],
}
```

- `missing_items`: `PHASE_REQUIREMENTS[next_phase]` のうち confirmed でない項目。
  各項目に日本語ラベル（`ITEM_LABELS`）と次アクション文（`NEXT_ACTION_HINTS`）を付す。
- `risk_items`: status が `risk` の全項目（Phase 4 サンプルの competition=risk を想定）。
  フェーズ前進と独立に「危険信号」として常に拾う。
- `current_phase == 5` のとき `next_phase=None` / `missing_items=[]`（リスクは表示継続）。

### §3.2 一覧ビュー

`MSORepository.list_sessions()` の結果を表で表示する。列:
会社 / Phase / 確率 / 期待収益 / 金額 / モデル / 評価日時 / session_id。
Streamlit の表はヘッダクリックでソートできるため、要件「金額・確率・期待収益で
ソート可能」を標準機能で満たす。データ 0 件なら投入手順を案内する。

### §3.3 詳細ビュー

一覧で選んだ 1 セッションを `get_session()` で取得して表示:

- 商談ヘッダ（会社 / 金額 / Phase / 確率 / 期待収益）。
- 不足項目ハイライト（`analyze_gaps()`）: 次フェーズ・不足項目・次アクション・
  リスク項目を警告色で。
- MEDDPICC 8 項目: status バッジ + evidence（quote / speaker / interpretation）。

### §3.4 デモデータ

`python -m db.repository` が `data/mso.db` に Phase 2 / Phase 4 サンプル 2 件を
LLM 非使用で投入する（既存の `__main__`）。`app.py` はこの DB を読む。
`data/` は `.gitignore` 対象（ローカル専用）。

---

## §4 ファイル構成

```
app.py                       # Streamlit エントリポイント（新規・読み取り専用）
core/phase.py                # analyze_gaps / PHASE_REQUIREMENTS 追加（既存を拡張）
docs/specs/phase11_streamlit_ui.md  # 本 SPEC
```

新規依存なし（`streamlit` は requirements.txt に既出）。

---

## §5 受け入れ基準

- `analyze_gaps()`:
  - Phase 2 サンプル → current=2、missing に economic_buyer / metrics を含む。
  - Phase 4 サンプル → current=4、risk_items に competition を含む。
  - 全 confirmed → current=5、next_phase=None、missing_items=[]。
- `judge_phase()` のリファクタ後も既存テスト・サンプル 2 件の判定が不変
  （Phase 2→2、Phase 4→4）。
- `streamlit run app.py` が起動し、一覧（2 件）と詳細が表示される。
- `app.py` は DB の書き込み API（`save_evaluation`）を呼ばない（読み取り専用）。

---

*Created: 2026-06-15 (Day 11 / Phase 11)*
