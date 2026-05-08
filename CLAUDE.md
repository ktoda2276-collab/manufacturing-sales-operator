# CLAUDE.md — Manufacturing Sales Operator (MSO)

このファイルは、Claude Code が本リポジトリで作業するときに参照する、プロジェクト固有の指示書です。セッションをまたいで一貫した方針で動けるよう、判断の前提と境界をここにまとめます。

---

## 1. プロジェクト概要

Manufacturing Sales Operator (MSO) は、**営業の需要予測（フォーキャスト）の精度を生成 AI で底上げし、その先にある製造の供給計画の精度向上に橋渡しする**個人プロジェクトです。製造業 BtoB 営業のフォーキャスト的中率は業界標準で 45-50% 程度にとどまり、この精度が製造側の供給計画精度の天井になっています。MSO は、商談文字起こしを LLM で MEDDPICC 8 項目に構造化し、フェーズと期待収益を客観的に算出することで、「営業担当者の主観」ではなく「顧客の言葉」を予測の根拠に据え直すことを目指します。

**設計思想**: 上流の品質を改善することで下流の品質を上げる。すなわち「営業の需要予測精度 → 製造の供給予測精度」の連鎖を、定性情報の機械的構造化によって駆動する。

---

## 2. 現在地と次のターゲット

- **現在**: v1 (MEDDPICC Analysis Engine) 開発中、**Week 1**
- **次のターゲット**: **DB スキーマ設計**（SQLite で商談・MEDDPICC 評価・フェーズ・期待収益を扱うテーブル設計）

ロードマップ全体（参考）:

| バージョン | 期間 | 内容 |
|---|---|---|
| v1 | Week 1-3 | MEDDPICC Analysis Engine（**今ここ**） |
| v2 | Week 4-7 | Enterprise Data Layer (Snowflake / RAG / Text-to-SQL) |
| v3 | Week 8-10 | Sales Forecast Analytics（動的確率、マルチ LLM ルーティング） |
| v4 | Week 11-12 | Manufacturing Bridge（設計ドキュメントのみ） |

---

## 3. 技術スタック（v1 時点）

- **Python**: 3.11.9（pyenv 経由でグローバル設定済み）
- **仮想環境**: `venv`（プロジェクトルート直下の `venv/`）
- **主要ライブラリ**: `anthropic`, `python-dotenv`, `streamlit`
- **DB**: SQLite（Python 標準ライブラリ `sqlite3` を使用、追加インストール不要）
- **LLM**:
  - メイン: **Claude Sonnet 4.6** (`claude-sonnet-4-6`) — MEDDPICC 抽出など中核処理
  - 補助: **Claude Haiku 4.5** (`claude-haiku-4-5`) — 動作確認・軽い処理・コスト最適化用途

評価・整形ツール（後で導入予定）:
- `pytest`（評価フレームワーク）
- `black`（フォーマッタ）
- `ruff`（リンター）

---

## 4. ディレクトリ構造の方針

v1 完成時の想定レイアウト:

```
manufacturing-sales-operator/
├── app.py                  # Streamlit エントリポイント
├── core/                   # ビジネスロジック
│   ├── meddpicc.py         #   MEDDPICC 8項目抽出
│   ├── phase.py            #   フェーズ判定
│   └── revenue.py          #   期待収益計算
├── db/                     # データアクセス層
│   ├── schema.sql          #   SQLite スキーマ定義
│   └── repository.py       #   CRUD 操作関数
├── prompts/                # LLM プロンプトテンプレート
│   └── meddpicc_extract.md
├── evals/                  # 評価フレームワーク
│   ├── dataset/            #   30件テストデータセット (JSON/YAML)
│   └── test_meddpicc.py    #   pytest テスト
├── hello_claude.py         # API 動作確認用（v1 完成後も保持）
├── requirements.txt
├── .env                    # API キー（git管理外）
├── .env.example            # テンプレート
├── .gitignore
├── README.md
└── CLAUDE.md
```

ファイル名・モジュール構成は実装段階で必要に応じて調整可。ただし**追加・分割・統合をする前にユーザーに一声かけてから**進めること。

---

## 5. コーディング規約

- **PEP 8 準拠**。フォーマッタは `black`、リンターは `ruff` を後で導入予定（導入後はそれらに従う）。
- **型ヒント推奨**。特に関数のシグネチャ（引数と戻り値）には可能な限り型を付ける。
- **docstring・コメントは日本語 OK、むしろ詳細に**。INTP のユーザーがコードを読んで学ぶことを意図しているため、「何を」だけでなく「なぜ」を残す。
- **関数名・変数名は英語の `snake_case`**。クラス名は `PascalCase`、定数は `UPPER_SNAKE_CASE`。
- 文字列は基本ダブルクォート（`black` のデフォルトに合わせる）。

---

## 6. API キー管理ポリシー

- **真のソース**: Bitwarden の `"Anthropic API Key - Manufacturing Sales Operator"` エントリ。キーを再発行・確認するときはここを参照。
- **実行時の参照**: `.env` に `ANTHROPIC_API_KEY=...` を書き、`python-dotenv` の `load_dotenv()` で読み込む。
- **`.env` は絶対に git に上げない**。`.gitignore` に登録済み（`.env`、`venv/`、`__pycache__/`、`*.pyc`）。コミット前に必ず `git status` で追跡対象外であることを確認。
- **`.env.example`** をテンプレートとして維持し、新しい環境変数を追加したら `.env.example` も同期する。
- API キーをコード・ログ・コミット・チャットへ誤って出力しないこと。Claude Code はログ出力時にキー値を `***` などでマスクする。

---

## 7. v1 のスコープ境界

### v1 でやる

- 商談文字起こしの入力・保存
- MEDDPICC 8 項目（M / E / D / D / P / I / C / C）の自動抽出
- フェーズ判定（各項目を `confirmed / partial / unconfirmed / risk` で評価し、Phase 1〜5 を判定）
- 期待収益計算（金額 × フェーズ別固定確率: 10% / 30% / 50% / 70% / 90%）
- 不足項目のハイライト + 次アクション提案
- 商談一覧ビュー（金額・確率・期待収益でソート可能）
- 評価フレームワーク（30 件の自作テストデータセット + pytest）

### v1 でやらない

- 実 CRM（Salesforce 等）連携
- 本物の企業データ（テストは全て架空データ）
- Snowflake 連携 → **v2** で実施
- 動的確率算出（過去実績ベース）→ **v3** で実施
- マルチ LLM ルーティング → **v3** で実施
- 製造システム実装 → **v4 は設計ドキュメントのみ**
- 多言語対応（日本語のみ）
- モバイル対応（デスクトップのみ）

スコープを越える提案・実装は、ユーザーに「これは v1 のスコープ外ですが進めますか？」と必ず確認してから着手する。

---

## 8. Claude Code への作業スタイル指示

- **不明点があれば実装前に質問する**。「たぶんこうだろう」で進めない。
- **大きな変更（新規ファイル群、構造変更、ライブラリ追加）の前に設計案を提示してユーザー確認を取る**。
- **既存ファイルは理由なく上書きしない**。特に `README.md`, `hello_claude.py`, `CLAUDE.md` は明示的指示なしに改変しない。
- **`requirements.txt` に新しいライブラリを追加するときは事前に確認**。理由（なぜ必要か、代替手段はないか）をセットで提示する。
- **コメント・docstring は丁寧に**。INTP のユーザーがコードを読んで学ぶ想定なので、「何を」だけでなく「なぜそうしたか」を書く。
- **Phase 単位で進める**。確認ポイントは Phase 完了時。それ以外は止めずに進める（ユーザーのグローバル方針）。
- **エラー時は自己判断で迂回せず、原因を共有してから相談する**（ユーザーのグローバル方針）。
- **コミットはユーザーの明示指示があるときだけ**。勝手に `git commit` しない。

---

## 9. デバッグとエラー対応

### 最初に疑う順序

1. **仮想環境が有効化されているか**。プロンプトに `(venv)` が出ているか／`which python` が `.../venv/bin/python` を指しているか。
2. **API キー関連のエラー**は `hello_claude.py` のエラー対処表（README 周辺会話で整理済み）を参照。`.env` の読み込み失敗・認証エラー・モデル名・残高・レート制限を順に確認。
3. **`import` エラー**: `pip install` で安易に解決する前に、本当に必要なライブラリかを確認する。標準ライブラリで足りないか、既存依存で代替できないかを先に検討。
4. **SQLite 関連**: スキーマ未作成・パス間違い・型不一致が典型。`db/schema.sql` の最新版が適用されているか確認。

### 動作確認の入り口

```bash
cd /Users/keiichirotoda/projects/manufacturing-sales-operator
source venv/bin/activate
python hello_claude.py   # API 接続が生きているかの最小確認
```

---

*Last updated: 2026-05-09*
