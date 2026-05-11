# MEDDPICC 抽出プロンプト設計メモ（v1）

このドキュメントは、MSO (Manufacturing Sales Operator) v1 における MEDDPICC 抽出プロンプトの設計判断と仕様を記述する。Claude Code に実装を依頼する際の仕様書を兼ねる。

- **対象**: v1（MEDDPICC Analysis Engine）
- **対象モデル**: Claude Sonnet 4.6（デフォルト）、Claude Haiku 4.5（評価フレームワークで比較）
- **設計議論日**: Day 3（2026-05-11）
- **想定読者**: 開発者（自分自身）、Claude Code、面接官

---

## 1. このドキュメントの目的

1. v1 における MEDDPICC 抽出プロンプトの**設計判断と根拠**を残す
2. Claude Code が実装する際の**仕様書**として機能する
3. 面接で「このプロンプトを見せて説明してください」と問われた際の**設計説明資料**として機能する

実装そのもの（プロンプト本文の MEDDPICC 8項目定義、Few-shot 架空商談 2 件、Python コード）は別ファイルで管理する。本ドキュメントは仕様のみを記述する。

---

## 2. 貫通する設計軸

本プロンプトは、3つの設計軸を Day 2 の DB スキーマ設計から継承し、貫通させている。

### 軸1: 可逆 vs 不可逆

後から困る判断は先回り、後から追加できる判断は YAGNI。

- **先回り**: `<transcript_source>` タグ（将来メール/Slack 等の入力ソース拡張に備える）、evidence の構造化（quote/speaker/interpretation の3フィールド）
- **YAGNI**: confidence スコア、時刻情報、ハルシネーション検証ロジック、議事録最大長制限

### 軸2: 判断は LLM、計算は Python

LLM が得意なこと（自然言語理解、文脈判断）と苦手なこと（決定論的計算、厳密ルール適用）を分離する。

- **LLM が担う**: MEDDPICC 8項目の status 判定（confirmed / partial / unconfirmed / risk）と evidence 抽出
- **Python が担う**: phase 判定、expected_revenue 計算、議事録最小長チェック、JSON パース、リトライ制御

これにより、LLM の非決定性が下流の計算に伝播しない。同じ status からは必ず同じ phase が返る。

### 軸3: Defense in Depth

各層が独立に防御を担い、全層を同時にすり抜ける確率を実用上ゼロまで下げる。

- **入力検証**: Python による議事録最小長チェック（API コール前）
- **指示の物理的分離**: XML タグで指示とデータを分離
- **出力形式制御**: プロンプトでのスキーマ明示 + Anthropic API の prefill + JSON パース失敗時の 1 回リトライ
- **型整合性**: LLM 出力の status と code の値域を、DB の CHECK 制約と一致させる

---

## 3. 入力設計

### 3.1 入力ソース

**録音文字起こし**を入力とする。営業担当者が書いた議事録（二次情報）ではなく、商談録音を文字起こしした一次情報を使う。

理由: MSO の設計思想「営業担当者の主観ではなく顧客の言葉を予測の根拠に据え直す」を成立させるには、営業フィルターが入っていない一次情報が必須。

### 3.2 話者ラベル形式

```
役職名（顧客/自社）:
発言内容
```

例:
```
山田部長（顧客）:
今期の生産性を15%上げたいんです。

田中AE（自社）:
ありがとうございます。目標値はどのくらいでお考えですか？
```

理由: 「顧客の発言」と「営業（自社）の発言」を Claude が区別できる必要がある。「予算は 2,000 万円」と言ったのが顧客か営業の願望かで MEDDPICC の判定が変わる。

### 3.3 時刻情報

**含めない**。v1 では evidence に時刻情報を要求しない。引用文字列のみで evidence を表現する。

理由: 可逆判断、YAGNI。v3 以降で時刻ベース分析が必要になったら追加する。

### 3.4 フィラー対応

**Claude にプロンプト指示で除去判断を委ねる**。前処理での除去はしない。

理由: 前処理ロジックの実装コストと、意味のあるフィラー（沈黙、逡巡）を誤除去するリスクを避ける。Claude にプロンプトで「evidence の quote 引用時、意味を変えない範囲でフィラーを除去してよい」と指示すれば足りる。

### 3.5 文字数

- **最小**: 500 文字（Python で事前チェック、未満は ValueError）
- **最大**: 制限なし（YAGNI）
- **想定範囲**: 5,000〜15,000 文字（30〜60 分商談相当）

### 3.6 商談コンテキスト

議事録本文と一緒に渡す情報:

- `amount`: 商談金額（整数、円）
- `description`: 業界 + 商談内容の 1 行サマリ（例: 「自動車部品メーカー向け生産管理システム導入」）

過去の商談履歴は v1 では渡さない（1 議事録 1 セッションで完結）。複数議事録の文脈統合は v2 以降のスコープ。

---

## 4. プロンプト全体構造

### 4.1 XML タグ階層

```xml
<!-- system プロンプト -->
あなたは製造業 BtoB 営業の MEDDPICC アナリストです。

<!-- user プロンプト -->
<meddpicc_definitions>
（MEDDPICC 8 項目の定義 - 実装時に詳細記述）
</meddpicc_definitions>

<status_criteria>
（confirmed / partial / unconfirmed / risk の判定基準）
</status_criteria>

<examples>
（Few-shot examples 2 件、各 example は <example> タグでラップ）
</examples>

<deal_context>
  <amount>商談金額（整数）</amount>
  <description>業界 + 商談内容の 1 行サマリ</description>
</deal_context>

<transcript_source>recorded_conversation_transcription</transcript_source>

<transcript>
（録音文字起こし本文）
</transcript>

<task>
（タスク指示 + 出力 JSON スキーマ）
</task>
```

### 4.2 XML タグ採用の根拠

- Anthropic 公式が推奨。Claude の学習データで XML タグが多用されているため、セクション区切りとして強く認識される
- 指示とデータの物理的分離（プロンプトインジェクション耐性も高まる）
- デバッグ性が高い（どのタグの記述が原因で精度が落ちているか切り分けやすい）
- 将来の拡張に強い（タグを追加するだけで新セクション導入できる）

### 4.3 各セクションの仕様

#### `<meddpicc_definitions>`

MEDDPICC 8 項目の定義を、製造業 BtoB 文脈で記述する。

| code | 項目 | 製造業文脈での具体例 |
|---|---|---|
| M | Metrics | 生産性向上 %、コスト削減円、不良率削減 % |
| E | Economic Buyer | 工場長、経営企画、決裁権限を持つ役員 |
| D1 | Decision Criteria | 価格 / 品質 / 納期 / サポートの優先順位 |
| D2 | Decision Process | 稟議フロー、競合比較プロセス、PoC 実施可否 |
| P | Paper Process | 購買部の手続き、契約書フォーマット要件 |
| I | Identify Pain | 設備老朽化、人手不足、品質問題、属人化 |
| C1 | Champion | 提案を社内で押してくれる味方の存在 |
| C2 | Competition | 競合他社、内製検討、現状維持の選好 |

**実装時の指示**: 各項目について、(1) 1〜2 行の定義、(2) 製造業 BtoB の具体例 2〜3 個、(3) 判定時の着眼点 1〜2 行、を記述する。

#### `<status_criteria>`

status の 4 段階の判定基準を明示する。

```
- confirmed: 顧客の発言に該当項目が明確に存在し、具体的な情報（数値、人名、プロセス等）が含まれる
- partial: 該当項目が示唆されているが、情報が断片的または推測を含む
- unconfirmed: 該当項目に関する発言が議事録中に存在しない
- risk: 該当項目に関して否定的な情報がある（例: 競合に流れそう、Champion が異動予定、価格がネックなど）
```

**判定基準明示の根拠**: これがないと Claude が独自解釈し、特に confirmed / partial の境界が出力ごとにブレる。評価フレームワークでの精度測定の信頼性に直結する。

#### `<examples>` (Few-shot 2 件)

2 件の架空商談を例示する。

- **例1**: Phase 2 相当（I, C1 が confirmed、M が partial、残りは unconfirmed）
- **例2**: Phase 4 相当（6〜7 項目が confirmed、C2 で risk が出る）

これにより 4 種類の status をすべてカバーする。

**実装方針**: 架空商談 2 件は Claude Code に「製造業 BtoB の架空商談文字起こし 2 件と、それに対応する MEDDPICC 抽出結果 JSON 2 件を生成」と依頼する。本ドキュメントには文面を含めない（スコープ外）。

#### `<deal_context>`

実装時に Python から動的に埋め込む。

```xml
<deal_context>
  <amount>20000000</amount>
  <description>自動車部品メーカー向け生産管理システム導入</description>
</deal_context>
```

#### `<transcript_source>`

入力ソース種別を明示。v1 では常に `recorded_conversation_transcription` 固定。

将来拡張時の候補値（参考）:
- `meeting_minutes_written`（議事録 = 二次情報）
- `email_thread`
- `slack_conversation`

これらは v2 以降の判断、v1 では実装しない。

#### `<transcript>`

録音文字起こし本文。話者ラベル「役職名（顧客/自社）:」形式。

#### `<task>`

タスク指示と出力 JSON スキーマ。

```
上記の商談録音文字起こしから、MEDDPICC 8項目を抽出してください。

ルール:
- 顧客の発言を予測の根拠の主軸とし、営業（自社）側の発言は補助情報として扱う
- evidence の quote はフィラー（「えーと」「あの」等）を意味を変えない範囲で除去してよい
- evidence の quote は議事録から引用する。議事録に存在しない発言を生成してはならない
- 該当する発言が見つからない項目は status を unconfirmed とし、evidence を null とする

出力は以下の JSON スキーマに厳密に従ってください:
（JSON スキーマ - 第5章を参照）
```

---

## 5. 出力 JSON スキーマ

### 5.1 スキーマ定義

```json
{
  "evaluations": [
    {
      "code": "M" | "E" | "D1" | "D2" | "P" | "I" | "C1" | "C2",
      "status": "confirmed" | "partial" | "unconfirmed" | "risk",
      "evidence": {
        "quote": "string (議事録からの引用、フィラー除去可)",
        "speaker": "string (発言者の表記、例: '山田部長（顧客）')",
        "interpretation": "string (Claude による解釈の 1 行サマリ)"
      } | null
    }
    // 配列長は固定で 8（8項目すべてを必ず出力）
  ]
}
```

### 5.2 evidence が null になる条件

`status` が `unconfirmed` の場合のみ。confirmed / partial / risk の場合は evidence の 3 フィールドすべてが必須。

### 5.3 派生フィールドを含めない理由

`phase` と `expected_revenue` はこの JSON に含めない。Python で `calculate_phase()` および `calculate_expected_revenue()` 関数により都度算出する。

理由:
- LLM の非決定性が下流計算に伝播するのを防ぐ（同じ status からは必ず同じ phase が返る保証）
- v3 で確率算出ロジックを動的化する際、Python 関数の差し替え 1 箇所で済む
- 「派生値はどこにも保存せず、必要なときに元データから計算する」原則を、LLM 出力 / DB / 表示の 3 層で貫通

### 5.4 DB スキーマとのマッピング

JSON 出力は DB の `meddpicc_evaluations` テーブルに 1 対 1 でマッピング可能。

```
JSON                              DB (meddpicc_evaluations)
─────────────────────────────────────────────────────────────
evaluations[].code            →  item_code
evaluations[].status          →  status
evaluations[].evidence.quote  →  evidence (JSON 文字列として保存)
evaluations[].evidence.*      →  evidence (同上、3フィールドまとめて JSON 化)
```

evidence は DB では TEXT カラムに JSON 文字列として保存する（quote / speaker / interpretation の 3 フィールドを 1 つの JSON にシリアライズ）。

---

## 6. エラーハンドリング仕様

### 6.1 入力検証（API コール前、Python）

```
議事録長 < 500 文字 → ValueError を投げる
```

最大長制限は v1 では行わない。

### 6.2 JSON 形式遵守の 3 層防御

**第 1 層: プロンプトでスキーマ明示**

`<task>` セクションに第 5 章の JSON スキーマを明記する。

**第 2 層: Anthropic API の prefill 機能**

API コール時、assistant メッセージの冒頭を `{` で prefill する。

```python
messages = [
    {"role": "user", "content": user_prompt},
    {"role": "assistant", "content": "{"}
]
```

これにより、Claude が前置きテキストや Markdown コードブロックを出力する余地が物理的に消える。応答は `"evaluations": [...]}` のように `{` の続きから始まる。

**第 3 層: JSON パース失敗時の 1 回リトライ**

```python
def extract_meddpicc(transcript, deal_context, max_retries=1):
    for attempt in range(max_retries + 1):
        try:
            response = call_claude(transcript, deal_context)
            # prefill で先頭の "{" は応答に含まれないので補完
            full_json = "{" + response
            return json.loads(full_json)
        except json.JSONDecodeError:
            if attempt == max_retries:
                raise MeddpiccExtractionError(
                    "JSON パースが 2 回失敗しました"
                )
            continue
```

リトライ回数は **1 回まで**（合計 2 回試行）に制限する。これ以上のリトライは行わない。

リトライ 1 回制限の根拠:
- LLM の非決定性により 2 回目で成功することがあるため、リトライに本質的な意味がある
- 一方、無限ループはコスト爆発インシデントの典型パターンであり、明示的な上限が必要
- 失敗が継続する場合、それはプロンプト側の設計問題のシグナルなので、リトライで誤魔化さず例外を投げて発見させる

### 6.3 ハルシネーション対策

**v1 ではプロンプトで縛るのみ**。検証ロジックは v1.5 以降で追加する。

プロンプトでの指示:
> evidence の quote は議事録から引用する。議事録に存在しない発言を生成してはならない。

v1 で実装しない理由:
- フィラー除去を許容しているため「ぴったり一致」検証はできず、正規化後の部分一致など実装が地味に重い
- v1 のゴールは抽出ロジックが動くこと。検証は v2 の評価フレームワーク構築と合わせて実装する方が自然
- 可逆判断（後から追加できる）

v1.5 以降での追加余地:
- DB の `evaluation_sessions` テーブルに transcript 全文を保存しているため、過去セッションも遡及的に検証可能
- Day 2 の append-only スキーマ設計の恩恵

---

## 7. モデル選定

### 7.1 デフォルトモデル

**Claude Sonnet 4.6** をデフォルトとする。

理由:
- MEDDPICC 抽出は判断精度が命の処理。Haiku は速度・コストで優位だが、文脈の機微判定で Sonnet に劣る
- v1 段階ではコストは問題にならない（30 件評価で数百円）
- 精度優先で選定したが、評価フレームワークで Haiku との比較を可能にし、将来のコスト最適化に備える

### 7.2 評価フレームワーク（v1 後半で実装）

`MODEL` を環境変数または設定で切り替え可能にし、Sonnet 4.6 と Haiku 4.5 の両方で同じテストデータを実行できる設計にする。

これにより、項目によっては Haiku で十分という発見があれば、実運用フェーズでコスト最適化が可能になる。

---

## 8. Claude Code 向け実装指示

このセクションは、本ドキュメントを基に Claude Code が実装する際の指示を含む。

### 8.1 作成すべきファイル（Day 4 以降の実装スコープ）

| ファイル | 内容 |
|---|---|
| `prompts/meddpicc_extraction.md` | このドキュメント本体（仕様書） |
| `prompts/meddpicc_extraction_prompt.py` | プロンプトテンプレート（XML タグ構造を含む完成版プロンプト文字列） |
| `prompts/examples/sample_transcript_phase2.md` | Few-shot 例 1（Phase 2 相当の架空商談） |
| `prompts/examples/sample_transcript_phase4.md` | Few-shot 例 2（Phase 4 相当の架空商談） |
| `prompts/examples/sample_extraction_phase2.json` | 例 1 に対応する期待出力 JSON |
| `prompts/examples/sample_extraction_phase4.json` | 例 2 に対応する期待出力 JSON |
| `core/extractor.py` | `extract_meddpicc()` 関数を含む抽出ロジック |
| `core/exceptions.py` | `MeddpiccExtractionError` 等のカスタム例外定義 |

ただし、Day 4 では `prompts/meddpicc_extraction.md` の作成 + Few-shot 例の生成までをスコープとする。Python コードは Day 5 以降。

### 8.2 実装時の禁則事項

- `phase` や `expected_revenue` を LLM の JSON 出力に含めない（Python 算出）
- `confidence` スコアを LLM に出させない（YAGNI）
- JSON パースのリトライを 1 回より多くしない（コスト爆発防止）
- 議事録最大長制限を実装しない（YAGNI）
- evidence の quote と議事録の検証ロジックを実装しない（v1.5 以降）

### 8.3 実装時に従うべき指針

- 設計判断に迷いが生じた場合、自分で決めずに「お伺い」として提示する（CLAUDE.md §8）
- DB の `meddpicc_evaluations` テーブルの CHECK 制約と、JSON 出力の `code` / `status` 値域を一致させる
- 動作確認は Claude Code に任せず、自分（人間）で実行する（Day 2 で確立した学習スタイル）

---

## 9. 確定事項サマリ（論点1〜5）

| # | 論点 | 決定 |
|---|---|---|
| 1A | プロンプト構造 | XML タグ採用 |
| 1B | Chain of Thought | なし、evidence で代替 |
| 1C | Few-shot | 2 件（Phase 2 相当 + Phase 4 相当） |
| 2A | 入力ソース | 録音文字起こし |
| 2A | 話者ラベル | 「役職名（顧客/自社）:」形式 |
| 2A | 時刻情報 | なし |
| 2A | フィラー対応 | Claude にプロンプト指示で委ねる |
| 2B | 議事録長 | 最小 500 文字、最大は制限なし |
| 2C | 商談コンテキスト | 金額 + 1 行サマリ |
| 2C | 過去議事録連結 | しない |
| 2C | 拡張性 | `<transcript_source>` タグで明示 |
| 3A | 出力単位 | 1 コールで 8 項目一気 |
| 3B | evidence 構造 | quote + speaker + interpretation の 3 フィールド |
| 3C | confidence | 出さない |
| 3D | JSON 構造 | フラット 8 項目並列 |
| 3D | 派生フィールド | LLM に出させず Python 算出 |
| 4A | デフォルトモデル | Sonnet 4.6（Haiku は評価で比較） |
| 4B | status 判定基準 | プロンプトで明示 |
| 4C | Few-shot 件数 | 2 件作る |
| 5A | JSON 形式遵守 | 3 層防御（プロンプト + prefill + 1 回リトライ） |
| 5B | 議事録最小長チェック | Python で事前検証 |
| 5C | ハルシネーション検証 | v1 ではプロンプトのみ、v1.5 以降で追加 |

---

## 10. 関連ドキュメント

- `CLAUDE.md`: プロジェクト全体の設計思想、Claude Code への作業指針
- `db/schema.sql`: DB スキーマ定義（meddpicc_evaluations テーブルの CHECK 制約を本ドキュメントの値域と一致させること）
- `README.md`: プロジェクト概要

---

*Last updated: 2026-05-11 (Day 3, 設計議論完了時点)*
*Next: Day 4 で Few-shot 架空商談 2 件を Claude Code に生成依頼*
