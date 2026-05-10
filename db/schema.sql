-- =============================================================================
-- MSO v1 — SQLite スキーマ定義
-- =============================================================================
-- このファイルは Manufacturing Sales Operator (MSO) v1 のデータベース定義です。
--
-- 設計方針:
--   - 3 テーブル正規化（deals / evaluation_sessions / meddpicc_evaluations）
--   - 評価は append-only。同じ商談に対する再評価は新しい session を作る
--     → 評価精度の時系列分析や、プロンプト改善の影響測定が可能になる
--   - DB レベルで Defense in Depth：CHECK 制約で不正値を弾き、
--     アプリ層の Python Enum と二重に整合性を担保する
--   - v3 で動的確率算出（過去実績ベース）に進化させるため、deals に
--     outcome / closed_at / actual_revenue を v1 時点から持たせる（先回り設計）
--
-- テーブルの役割:
--   deals                  : 商談マスタ。1 商談 = 1 行。
--   evaluation_sessions    : 評価セッション。1 商談に対し N 回の評価を保持。
--   meddpicc_evaluations   : 評価明細。1 セッションあたり 8 行（M/E/D1/D2/P/I/C1/C2）。
--
-- 適用方法:
--   db/init_db.py から読み込まれ、プロジェクトルートの mso.db に対して実行される。
-- =============================================================================

-- SQLite は外部キー制約をデフォルトで OFF にするため、接続ごとに ON が必要。
-- スキーマ適用時にも有効化しておくことで、以降の DDL/DML で制約が機能する。
PRAGMA foreign_keys = ON;


-- -----------------------------------------------------------------------------
-- deals: 商談マスタ
-- -----------------------------------------------------------------------------
-- 1 商談 = 1 行。商談の基本情報と最終結果（outcome）を保持する。
-- outcome / closed_at / actual_revenue は v1 では主に手入力／テストデータ投入で
-- 埋まる。v3 でこれらを集計して業種×金額帯×フェーズ別の動的確率を算出する。
-- -----------------------------------------------------------------------------
CREATE TABLE deals (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    company         TEXT    NOT NULL,                         -- 顧客企業名
    amount          INTEGER NOT NULL,                         -- 商談金額（単位: 円）
    deal_date       DATE    NOT NULL,                         -- 商談開始日
    outcome         TEXT    NOT NULL DEFAULT 'open'           -- 商談の最終結果
                    CHECK (outcome IN ('open', 'won', 'lost', 'abandoned')),
    closed_at       DATETIME,                                 -- クローズ日時（open のうちは NULL）
    actual_revenue  INTEGER,                                  -- 実受注額（won のときのみ値が入る想定）
    created_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);


-- -----------------------------------------------------------------------------
-- evaluation_sessions: 評価セッション
-- -----------------------------------------------------------------------------
-- 1 つの商談に対して、ある時点の transcript を LLM で評価した「1 回分」を表す。
-- 同じ商談を再評価したら新しい行が追加される（append-only）。
-- transcript は将来 RAG（v2）の対象にもなるため、原文をそのまま保持する。
-- -----------------------------------------------------------------------------
CREATE TABLE evaluation_sessions (
    id            INTEGER  PRIMARY KEY AUTOINCREMENT,
    deal_id       INTEGER  NOT NULL,                          -- 紐づく商談
    transcript    TEXT     NOT NULL,                          -- 議事録テキスト原文
    model         TEXT     NOT NULL,                          -- 使用 LLM モデル名（例: 'claude-sonnet-4-6'）
    evaluated_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, -- 評価実行時刻
    created_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (deal_id) REFERENCES deals(id)
);


-- -----------------------------------------------------------------------------
-- meddpicc_evaluations: 評価明細
-- -----------------------------------------------------------------------------
-- 1 セッションあたり 8 行（MEDDPICC の 8 項目それぞれに 1 行）が入る想定。
-- 同じ D / C が 2 つずつあるため、項目コードは D1/D2/C1/C2 で区別する：
--   M  : Metrics
--   E  : Economic Buyer
--   D1 : Decision Criteria
--   D2 : Decision Process
--   P  : Paper Process
--   I  : Identify Pain
--   C1 : Champion
--   C2 : Competition
-- evidence は LLM が判断根拠とした transcript の抜粋／要約を保持する。
-- -----------------------------------------------------------------------------
CREATE TABLE meddpicc_evaluations (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  INTEGER NOT NULL,                             -- 紐づく評価セッション
    item_code   TEXT    NOT NULL                              -- MEDDPICC 8 項目コード
                CHECK (item_code IN ('M', 'E', 'D1', 'D2', 'P', 'I', 'C1', 'C2')),
    status      TEXT    NOT NULL                              -- 4 段階の合意ステータス
                CHECK (status IN ('confirmed', 'partial', 'unconfirmed', 'risk')),
    evidence    TEXT,                                         -- 判断根拠（NULL 許容）
    FOREIGN KEY (session_id) REFERENCES evaluation_sessions(id)
);
