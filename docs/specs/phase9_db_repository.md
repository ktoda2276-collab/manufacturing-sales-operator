# Phase 9 SPEC: DB 接続層 (Repository)

## §1 目的とスコープ

### 目的

Phase 8 で確立した `core/pipeline.run_pipeline()` の戻り値を SQLite
(`db/schema.sql` の 3 テーブル) に永続化し、過去セッションを照会する
データアクセス層 `db/repository.py` (`MSORepository` クラス) を実装する。

UI (Phase 11) や評価フレームワーク (Phase 10) は本クラス経由でのみ DB に
触れる。SQL を直書きするのは本ファイルに閉じ込め、上位層は Python の
メソッド呼び出しだけで永続化・照会できる状態にする。

### Repository クラスの責務

- **pipeline 結果の永続化** (`save_evaluation`): `run_pipeline()` が返す
  `dict` を 3 テーブル (`deals` / `evaluation_sessions` /
  `meddpicc_evaluations`) に 1 トランザクションで書き込む。
- **過去セッションの照会** (`get_session` / `list_sessions`): 永続化済みの
  評価を読み出す。フェーズ・期待収益は保存せず読み出し時に再計算する
  (§4 参照)。
- **スキーマの自動適用** (`__init__`): 接続先 DB にテーブルが無ければ
  `db/schema.sql` を適用する。`db/init_db.py` (スキーマ適用専用スクリプト)
  とは別系統だが、同じ `schema.sql` を唯一のソースとして共有する。

### 含まないもの

- マイグレーション (スキーマ進化の差分適用)。v2 以降で必要になったら検討。
- `outcome` / `closed_at` / `actual_revenue` の更新 API。v3 の動的確率算出で
  実績を埋める段になってから設計する (今は schema にカラムだけ先回りで存在)。
- 接続プール・並行制御。v1 は Streamlit 単一ユーザー想定で単一接続で足りる。
- LLM 呼び出し。本層は決定論的な Python のみ (judge_phase / calc_expected_revenue
  はいずれも純関数で anthropic 非依存)。

---

## §2 設計判断

### 軸 1 (可逆 vs 不可逆): phase / revenue は保存せず再計算

`pipeline_result` は `phase` (int) と `revenue` (dict) を含むが、`schema.sql`
の 3 テーブルにはこれらを格納するカラムが**無い**。これは設計上の欠落では
なく方針である:

- `phase` は MEDDPICC 8 項目の `status` から決定論的に算出される
  (`core/phase.judge_phase`)。
- `revenue` は `deal_amount` + `phase` + `status` から決定論的に算出される
  (`core/revenue.calc_expected_revenue`)。

これらは**派生値**であり、永続化すると判定規則・確率定数を変更したときに
DB の値が陳腐化する (schema.sql 冒頭コメントの「append-only / v3 で動的確率」
方針と整合)。したがって:

- **save 時**: `phase` / `revenue` は書き込まない (生の入力だけを保存)。
- **get 時**: 保存済みの `status` と `amount` から `judge_phase` /
  `calc_expected_revenue` を呼び、その場で再計算して返す。

結果として round-trip テスト (save → get) で `phase` まで含めて検証でき、
判定ロジックと永続化の整合も同時に確認できる。

> ユーザー確認済み (2026-06-04): 「保存せず get で再計算」を採用。

### 軸 2 (判断は LLM、計算は Python): Repository は SQL とマッピングのみ

Repository は業務ロジック (フェーズ判定・確率計算) を**持たない**。
判定は `core.phase` / `core.revenue` に委譲し、本層の責務は

1. `dict` ⇄ SQL 行のマッピング (ネストキー ⇄ `item_code`、evidence の
   JSON シリアライズ)、
2. トランザクション制御、

のみに限定する。pipeline の「調整役」と同じ思想を DB 境界に適用する。

### 軸 3 (Defense in Depth): 例外は透過、検証は DB の CHECK 制約に委ねる

- `phase.py` / `revenue.py` / `pipeline.py` と同方針で、**例外は握りつぶさず
  透過**する。
- `status` enum (`confirmed` / `partial` / `unconfirmed` / `risk`) や
  `item_code` (`M`/`E`/`D1`/`D2`/`P`/`I`/`C1`/`C2`) の妥当性は
  `schema.sql` の `CHECK` 制約が最終防壁。不正値は `sqlite3.IntegrityError`
  として透過する (アプリ層で重複検証しない = DRY)。
- 上位の `pipeline` / `extractor` が既に値を検証済みのため、Repository での
  事前検証は最小限 (マッピング時の未知キー検出のみ)。

### transcript / model の扱い: 任意引数を追加

`schema.sql` の `evaluation_sessions` は `transcript` と `model` が
`NOT NULL` だが、指定の `save_evaluation(deal_name, deal_amount,
pipeline_result)` シグネチャには含まれない。これを **任意引数の追加**で
解消する:

```python
def save_evaluation(self, deal_name, deal_amount, pipeline_result,
                    transcript="", model=DEFAULT_MODEL) -> int:
```

- 指定の 3 引数だけで呼べる (テスト・最小利用) 。
- pipeline 統合時 (Phase 11) は実議事録・実モデル名を渡せる。
- `NOT NULL` は空文字列 `""` で満たされる (NULL ではないため OK)。

> ユーザー確認済み (2026-06-04): 「任意引数を追加」を採用。

### score フィールドについて (タスク記述との差分)

タスク記述では meddpicc 各項目を `{"status", "score", "evidence"}` と
しているが、`core/extractor.py` の実際の戻り値および
`prompts/examples/sample_extraction_*.json` は `{"status", "evidence"}` の
2 キーのみで `score` を持たない。`schema.sql` の `meddpicc_evaluations` にも
`score` カラムは無い。したがって本実装は **`status` と `evidence` のみ永続化**
し、`score` が存在しても無視する (`.get` で防御的に扱う)。実装は将来 `score`
が追加されても壊れない。

---

## §3 インターフェース仕様

### 定数

```python
DEFAULT_MODEL = "claude-opus-4-7"   # extractor.MODEL と同値 (疎結合のため再定義)

# MEDDPICC ネストキー (extractor 戻り値) → schema item_code の対応
ITEM_CODE_MAP = {
    "metrics": "M", "economic_buyer": "E",
    "decision_criteria": "D1", "decision_process": "D2",
    "paper_process": "P", "identify_pain": "I",
    "champion": "C1", "competition": "C2",
}
# 逆引き (get 時の item_code → ネストキー復元用)
CODE_TO_KEY = {v: k for k, v in ITEM_CODE_MAP.items()}
```

### メソッドシグネチャ一覧

| メソッド | シグネチャ | 戻り値 |
|---|---|---|
| 初期化 | `__init__(self, db_path: str)` | `None` |
| 永続化 | `save_evaluation(self, deal_name: str, deal_amount: int, pipeline_result: dict, transcript: str = "", model: str = DEFAULT_MODEL) -> int` | `session_id` |
| 単件照会 | `get_session(self, session_id: int) -> dict` | セッション dict |
| 一覧照会 | `list_sessions(self, limit: int = 20) -> list[dict]` | サマリ dict のリスト |
| クローズ | `close(self) -> None` | `None` |

`MSORepository` は context manager (`__enter__` / `__exit__`) もサポートし、
`with MSORepository(path) as repo:` で安全にクローズできる。

### `__init__(self, db_path)`

1. `db_path` の親ディレクトリを `mkdir(parents=True, exist_ok=True)` で確保
   (`data/mso.db` のような未作成ディレクトリ下のパスでも接続可能にする)。
2. `sqlite3.connect(db_path)` で接続を保持。`row_factory = sqlite3.Row`
   (列名アクセス可能化)。
3. `PRAGMA foreign_keys = ON;` を発行 (SQLite は接続毎に FK が OFF のため)。
4. `sqlite_master` に `deals` テーブルが無ければ `db/schema.sql` を
   `executescript` で適用 (テーブル未作成時のみ。既存 DB は再適用しない =
   `CREATE TABLE` 重複エラー回避)。

### `save_evaluation(...) -> int`

`run_pipeline()` の戻り値 `dict` を 3 テーブルに 1 トランザクションで書く。

- `deals`: `company = deal_name`, `amount = deal_amount`,
  `deal_date = DATE('now')` (SQL 側で当日日付。`outcome` は default `'open'`)。
- `evaluation_sessions`: `deal_id` (直前の lastrowid), `transcript`, `model`。
- `meddpicc_evaluations`: `pipeline_result["meddpicc_evaluations"]` の 8 項目を
  `ITEM_CODE_MAP` で `item_code` に変換し、`status` と
  `evidence`(JSON 文字列) を 8 行 `executemany` で挿入。

戻り値は新規 `evaluation_sessions.id` (= session_id)。

### `get_session(session_id) -> dict`

```python
{
    "session_id": int,
    "deal": {
        "id": int, "company": str, "amount": int, "deal_date": str,
        "outcome": str, "closed_at": str | None,
        "actual_revenue": int | None, "created_at": str,
    },
    "transcript": str,
    "model": str,
    "evaluated_at": str,
    "meddpicc_evaluations": {                # MEDDPICC 正準順 (ネストキー)
        "metrics": {"status": str, "evidence": dict | None},
        ... (8 項目)
    },
    "phase": int,        # 再計算 (judge_phase)
    "revenue": dict,     # 再計算 (calc_expected_revenue 戻り値)
}
```

存在しない `session_id` の場合は `ValueError` (DB 例外ではなく明示的に送出)。

### `list_sessions(limit=20) -> list[dict]`

`evaluated_at` 降順 (新しい順) に最大 `limit` 件。各要素:

```python
{
    "session_id": int, "deal_id": int, "company": str, "amount": int,
    "model": str, "evaluated_at": str,
    "phase": int, "probability": float, "expected_revenue": int,  # 再計算
}
```

`phase` / `expected_revenue` は v1 要件「商談一覧ビュー(金額・確率・期待収益で
ソート可能)」(CLAUDE.md §7) のため再計算して含める。N+1 を避けるため、対象
session の meddpicc 明細を 1 クエリでまとめて取得し session 毎に集約する。

---

## §4 `run_pipeline()` との結合点 (フィールド → テーブル対応表)

| pipeline_result のフィールド | 保存先テーブル.カラム | 補足 |
|---|---|---|
| (引数) `deal_name` | `deals.company` | save_evaluation の引数 |
| (引数) `deal_amount` | `deals.amount` | save_evaluation の引数 |
| (生成) 当日日付 | `deals.deal_date` | `DATE('now')` |
| (引数) `transcript` | `evaluation_sessions.transcript` | 任意引数 (default `""`) |
| (引数) `model` | `evaluation_sessions.model` | 任意引数 (default `DEFAULT_MODEL`) |
| `meddpicc_evaluations[k]["status"]` | `meddpicc_evaluations.status` | 8 行 |
| `meddpicc_evaluations[k]["evidence"]` | `meddpicc_evaluations.evidence` | JSON 文字列化 |
| `meddpicc_evaluations` のキー `k` | `meddpicc_evaluations.item_code` | `ITEM_CODE_MAP` で変換 |
| `phase` | — (保存しない) | get 時に `judge_phase` で再計算 |
| `revenue` | — (保存しない) | get 時に `calc_expected_revenue` で再計算 |

---

## §5 異常系の方針

| 発生源 | 例外 | 補足 |
|---|---|---|
| 未知の MEDDPICC キー | `KeyError` | `ITEM_CODE_MAP[k]` で透過。extractor 破損の検出点。 |
| `status` enum 外 / `item_code` 不正 | `sqlite3.IntegrityError` | schema.sql の `CHECK` 制約が最終防壁。透過。 |
| FK 違反 (本来発生しない) | `sqlite3.IntegrityError` | `PRAGMA foreign_keys = ON` 下で透過。 |
| トランザクション途中の失敗 | 元の例外 | `with conn:` が自動 rollback。3 テーブルの原子性を保証。 |
| 存在しない `session_id` | `ValueError` | `get_session` が明示送出。 |

- schema.sql の `CHECK` 制約 (`outcome` / `item_code` / `status` の enum) と
  アプリ層 (extractor → pipeline → phase/revenue の検証) で **二重防御**。
  Repository 自身は重複検証せず、不正は DB 例外として透過する (軸 3 / DRY)。

---

## §6 動作確認 (受け入れ基準)

`python -m db.repository` で以下を実行 (LLM は呼ばず、既存サンプル JSON を使用):

1. テスト用 DB (`data/mso.db`) を作り直し (既存なら削除) → スキーマ自動適用。
2. **Phase 2 サンプル** (`prompts/examples/sample_extraction_phase2.json`) を
   `pipeline_result` 形に整形 → `save_evaluation` → `get_session` で
   round-trip。`phase == 2`、8 項目の status / evidence 一致を確認。
3. **Phase 4 サンプル** (`sample_extraction_phase4.json`) も同様。`phase == 4`。
4. `list_sessions()` で 2 件が新しい順に返ることを確認。
5. `data/mso.db` ファイルが生成されることを確認。

> 注: `data/` は `.gitignore` に追加し、テスト DB をコミット対象外にする
> (`mso.db` と同方針)。

---

## §7 将来の拡張ポイント (v1 スコープ外)

- **outcome 更新 API** (v3): `mark_won` / `mark_lost` 等で実績を記録し、
  動的確率算出の母集団にする。
- **マイグレーション** (v2): schema 進化時の差分適用。今は schema.sql 再適用
  のみ。
- **dataclass / 型付き戻り値** (v3): `dict` から属性アクセスへ。pipeline と
  同じく呼び出し側が増えてから検討。
- **ページング** (UI 拡張時): `list_sessions` に offset を追加。
