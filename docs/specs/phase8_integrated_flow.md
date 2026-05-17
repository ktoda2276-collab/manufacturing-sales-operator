# Phase 8 SPEC: 統合フロー (Integrated Flow)

## §1 目的とスコープ

### 目的

Phase 5 (プロンプト生成) ・ Phase 6 (`core/extractor.py`) ・ Phase 7
(`core/phase.py` / `core/revenue.py`) で実装した 3 層を、一気通貫で呼び出す
統合層を `core/pipeline.py` として実装する。v1 完成形における**唯一のエントリ
ポイント**として、後段の DB 永続化 (Phase 9) や UI (Phase 11) は本関数を呼ぶ
だけで MEDDPICC 評価 → フェーズ → 期待収益までを得られる状態にする。

### 含むもの

- 議事録 (`str`) + 商談金額 (`int`) を受け取り、MEDDPICC 評価・フェーズ・
  期待収益を統合 `dict` で返す関数 `run_pipeline()`。
- `core/extractor.py` の返却値 (ネスト構造) を `core/phase.py` /
  `core/revenue.py` が要求する flat 構造へ変換するロジック (pipeline の責務)。
- `python -m core.pipeline` で実行できる `__main__` ブロック (動作確認用 CLI)。

### 含まないもの

- データ永続化 (SQLite への書き込み)。これは **Phase 9** で扱う。
- Streamlit UI。**Phase 11** で扱う。
- リトライ機構・タイムアウト制御。**v3** で扱う (現時点では SDK / 上位層の
  例外を素通しする)。
- モック extractor (LLM を呼ばずに固定 JSON を返す差し替え機構)。**Phase 10**
  の評価フレームワーク導入時に設計する。

---

## §2 設計判断

### 軸 1 (可逆 vs 不可逆): 戻り値は dict

統合結果は `dict` で返す。`dataclass` 化やレスポンス型クラスの導入は v3 以降
でも遅くない (呼び出し側が少ない / 内部 API)。今は dict のままにして、必要に
なってから型を被せる。**可逆な判断**であり、過剰設計を避ける YAGNI に従う。

### 軸 2 (判断は LLM、計算は Python): pipeline は調整役のみ

`run_pipeline()` は業務ロジックを持たない。

- LLM 判断: `extractor.extract_meddpicc()` に委譲。
- 決定論的計算: `phase.judge_phase()` / `revenue.calc_expected_revenue()` に委譲。
- pipeline 自身の責務は「順序制御」と「ネスト → flat の構造変換」のみ。

判定規則や確率定数を pipeline 内で再実装しないこと (DRY 違反かつ仕様の二重管理
リスク)。

### 軸 3 (Defense in Depth): 例外は透過、検証は層ごと

- pipeline 自身は**入力契約 (transcript / deal_amount) の最小検証のみ**実施。
- 3 モジュールが投げる例外は `try` で握りつぶさず**素通し**する。各層が自分の
  責務範囲で検証を行う方が、エラーの所在を特定しやすい。
- `extractor` 戻り値の MEDDPICC キー検証は `phase.judge_phase()` が実施するため、
  pipeline でも重複チェックしない (DRY)。

### 入力契約: 引数は 2 つのみ

`run_pipeline(transcript, deal_amount)` で受け取るのは「議事録」と「商談金額」
のみ。商談ID・顧客名・担当者など**商談メタデータは Phase 9 (DB 層) の責務**と
して切り離す。pipeline は「1 つの議事録から MEDDPICC + フェーズ + 期待収益を
出す」ことだけに集中させる。

---

## §3 インターフェース仕様

### シグネチャ

```python
def run_pipeline(transcript: str, deal_amount: int) -> dict:
```

### 入力契約

| 引数 | 型 | 制約 |
|---|---|---|
| `transcript` | `str` | 空文字列・空白のみ (`strip()` で空になる) は不可。`ValueError`。 |
| `deal_amount` | `int` | 0 以上必須 (`< 0` で `ValueError`)。0 は許容 (期待収益も 0 になる)。 |

注: 議事録の最小長・話者ラベル形式などのコンテンツ検証は本関数では行わない
(Phase 10 評価フレームワークで扱う)。

### 出力契約

```python
{
    "meddpicc_evaluations": dict,  # extractor の戻り値 (ネスト構造、加工なし)
    "phase": int,                   # 0〜5
    "revenue": dict,                # calc_expected_revenue の戻り値 (加工なし)
}
```

- `meddpicc_evaluations`: `core/extractor.py` が返す 8 項目並列ネスト構造
  (各値は `{"status": ..., "evidence": {...}}`)。pipeline は加工しない。
- `phase`: `core/phase.py` が返す `int` (0〜5)。
- `revenue`: `core/revenue.py` が返す dict (`deal_amount` / `phase` /
  `probability` / `expected_revenue` / `factors` の 5 キー)。

### 異常系

| 発生源 | 例外 | 補足 |
|---|---|---|
| pipeline 入力検証 | `ValueError` | `transcript` 空、`deal_amount` 負。 |
| `extractor` | `RuntimeError` / `anthropic.APIError` 系 / `json.JSONDecodeError` | 透過。Phase 10 で堅牢化。 |
| `phase` / `revenue` | `ValueError` | MEDDPICC キー不一致、status enum 外、phase 範囲外など。透過。 |

---

## §4 内部処理フロー

1. **入力バリデーション**:
   - `transcript.strip() == ""` → `ValueError("transcript は空文字列不可")`。
   - `deal_amount < 0` → `ValueError("deal_amount は 0 以上必須: {n}")`。
2. **MEDDPICC 抽出**: `nested = extractor.extract_meddpicc(transcript)` を呼ぶ。
   API 例外・JSON パース失敗は透過。
3. **ネスト → flat 変換**: `flat = {k: v["status"] for k, v in nested.items()}`。
   これは pipeline の責務 (各 3 モジュールでは持たない構造変換)。
4. **フェーズ判定**: `phase = phase_module.judge_phase(flat)`。
5. **期待収益計算**:
   `revenue = revenue_module.calc_expected_revenue(deal_amount, phase, flat)`。
6. **統合 dict 返却**:
   ```python
   return {
       "meddpicc_evaluations": nested,  # ネストのまま返す (evidence を保持)
       "phase": phase,
       "revenue": revenue,
   }
   ```

### 設計メモ: なぜ flat 変換を pipeline に置くか

- `core/phase.py` / `core/revenue.py` の入力契約を **flat dict に統一**したのは
  Phase 7 SPEC で決定済み (テスト性とシリアライズ性の確保)。
- extractor 側の返却値はネスト構造のまま保持したい (evidence の保持価値が高い)。
- 「ネスト → flat」は両者をつなぐ**境界変換**であり、調整役である pipeline が
  持つのが自然。3 モジュールのどれかに寄せると役割が滲む。

### 設計メモ: extractor 戻り値の形式チェックを pipeline で行わない理由

- `phase.judge_phase()` が MEDDPICC キーの完全一致を検証している。
- ここで pipeline 側でも同じチェックを書くと **DRY 違反**になり、SPEC 変更時に
  両方の修正が必要になる。
- 「extractor が壊れた JSON を返した」場合の発見は `phase.judge_phase()` の
  `ValueError` で十分行える (例外メッセージから原因を特定可能)。

---

## §5 受け入れ基準

### 正常系 (API 実呼び出し)

| ケース | 入力 | 期待 |
|---|---|---|
| Phase 2 サンプル | `prompts/examples/sample_transcript_phase2.md`, deal=10,000,000 | `phase == 2` (LLM 揺らぎで ±1 は許容)、`revenue["expected_revenue"]` は正の整数 |
| Phase 4 サンプル | `prompts/examples/sample_transcript_phase4.md`, deal=10,000,000 | `phase == 4` (LLM 揺らぎで ±1 は許容)、`revenue["expected_revenue"]` は正の整数 |

「LLM 揺らぎで ±1 を許容」とした理由: Phase 6 の `extract_meddpicc()` は決定論的
ではなく、サンプル議事録でも稀に `partial` ↔ `confirmed` がブレる可能性がある。
Phase 8 の動作確認はあくまで「3 層が連結して動く」ことの検証であり、抽出精度
そのものの評価は Phase 10 評価フレームワークの責務とする。

### 異常系 (API を叩かない)

| ケース | 入力 | 期待 |
|---|---|---|
| 空文字列 | `transcript=""`, `deal_amount=1` | `ValueError` |
| 空白のみ | `transcript="   \n"`, `deal_amount=1` | `ValueError` |
| 負値 | `transcript="...有効文..."`, `deal_amount=-1` | `ValueError` |

### 戻り値構造

- トップレベルキー: `meddpicc_evaluations` / `phase` / `revenue` の 3 つ。
- `revenue` 配下: `deal_amount` / `phase` / `probability` / `expected_revenue` /
  `factors` の 5 つ。
- `meddpicc_evaluations` 配下: MEDDPICC 8 項目すべて (`metrics` /
  `economic_buyer` / `decision_criteria` / `decision_process` / `paper_process` /
  `identify_pain` / `champion` / `competition`)。

---

## §6 将来の拡張ポイント (v1 スコープ外)

- **dataclass 化** (v3): `PipelineResult` のような型を導入し、`dict` アクセス
  から属性アクセスへ。呼び出し側 (UI / DB) が増えてから検討。
- **error フィールド方式** (v2 UI 検討時): 例外を投げる代わりに
  `{"status": "error", "message": ..., ...}` を返す方式。UI 表示で「失敗」を
  ユーザーフレンドリーに見せたくなったときに検討。
- **リトライ機構** (v3): `tenacity` などで extractor の API 呼び出しを
  指数バックオフ再試行。本番運用で必要になったときに pipeline か extractor の
  どちらに置くか改めて判断。
- **モック extractor** (Phase 10 評価フレームワーク): `extract_meddpicc` を
  差し替えて固定 JSON を返すモードを `run_pipeline` レベルで持つ。Phase 10 で
  依存注入 (引数 or env) の形を決める。
- **ストリーミング**: 抽出途中の中間状態を UI に流す。UX 改善時に検討。
