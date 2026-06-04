"""DB 接続層 (Phase 9)

`core/pipeline.run_pipeline()` が返す統合結果 dict を SQLite
(`db/schema.sql` の 3 テーブル) に永続化し、過去セッションを照会する
データアクセス層。UI (Phase 11) や評価フレームワーク (Phase 10) は本クラス
経由でのみ DB に触れ、SQL の直書きは本ファイルに閉じ込める。

設計判断 (詳細は docs/specs/phase9_db_repository.md):
- 軸 1 (可逆 vs 不可逆): phase / revenue は schema に保存カラムが無く、
  かつ MEDDPICC status + amount から決定論的に再計算できる「派生値」なので
  永続化しない。get 時に judge_phase / calc_expected_revenue で再計算する。
  判定規則・確率定数を変えても DB が陳腐化しない (append-only 方針と整合)。
- 軸 2 (判断は LLM、計算は Python): Repository は業務ロジックを持たず、
  dict ⇄ SQL 行のマッピングとトランザクション制御だけを担う。フェーズ判定・
  確率計算は core.phase / core.revenue (いずれも anthropic 非依存の純関数)
  に委譲する。
- 軸 3 (Defense in Depth): 例外は握りつぶさず透過。status / item_code の
  妥当性は schema.sql の CHECK 制約を最終防壁とし、アプリ層で重複検証しない
  (DRY)。不正値は sqlite3.IntegrityError として透過する。

参照:
- docs/specs/phase9_db_repository.md: 本モジュールの仕様
- db/schema.sql: テーブル定義 (CHECK 制約を含む唯一のスキーマソース)
- db/init_db.py: スキーマ適用専用スクリプト (本クラスとは別系統だが schema 共有)
- core/phase.py / core/revenue.py: 派生値 (phase / revenue) の再計算ロジック
"""
import json
import sqlite3
from pathlib import Path

from core.phase import judge_phase
from core.revenue import calc_expected_revenue

# 使用想定の既定モデル名。extractor.MODEL と同値だが、extractor を import すると
# anthropic / dotenv まで巻き込むため、疎結合のため定数を再定義する。
# (Repository は LLM 非依存で動けることを保証したい)
DEFAULT_MODEL = "claude-opus-4-7"

# MEDDPICC ネストキー (extractor 戻り値の snake_case) → schema の item_code 対応。
# D / C が 2 つずつあるため D1/D2・C1/C2 で区別する (schema.sql のコメント参照)。
ITEM_CODE_MAP = {
    "metrics": "M",
    "economic_buyer": "E",
    "decision_criteria": "D1",
    "decision_process": "D2",
    "paper_process": "P",
    "identify_pain": "I",
    "champion": "C1",
    "competition": "C2",
}
# get 時に item_code → ネストキーを復元するための逆引き表。
CODE_TO_KEY = {code: key for key, code in ITEM_CODE_MAP.items()}

# schema.sql の場所 (本ファイルと同じ db/ ディレクトリ)。
# 絶対パスで固定し「どこから実行したか」に依存しないようにする (init_db.py と同方針)。
SCHEMA_PATH: Path = Path(__file__).parent / "schema.sql"


class MSORepository:
    """MSO の SQLite 永続化を担うリポジトリ。

    1 インスタンス = 1 DB 接続。Streamlit 単一ユーザー想定 (v1) では
    接続を保持し続けて問題ない。複数スレッド共有はしない前提。

    使い方:
        with MSORepository("data/mso.db") as repo:
            session_id = repo.save_evaluation("○○製作所", 10_000_000, result)
            session = repo.get_session(session_id)
    """

    def __init__(self, db_path: str) -> None:
        """DB に接続し、テーブルが未作成なら schema.sql を適用する。

        Args:
            db_path: SQLite ファイルのパス。親ディレクトリが無ければ作成する
                (例: "data/mso.db" のように未作成ディレクトリ下でも接続可能)。

        Notes:
            - row_factory に sqlite3.Row を設定し、列名でのアクセスを可能にする。
            - SQLite は接続毎に外部キー制約が OFF のため、毎接続で
              PRAGMA foreign_keys = ON を発行する。
            - schema 適用は「deals テーブルが存在しないとき」のみ実行する。
              schema.sql の CREATE TABLE は IF NOT EXISTS を付けない方針なので、
              既存 DB に再適用すると "table already exists" になる。それを避ける。
        """
        self.db_path = db_path

        # ":memory:" や親が既存のパスでは mkdir 不要だが、exist_ok=True なので
        # 常に呼んで害はない。data/mso.db のような未作成ディレクトリを吸収する。
        parent = Path(db_path).parent
        if str(parent) not in ("", "."):
            parent.mkdir(parents=True, exist_ok=True)

        self.conn = sqlite3.connect(db_path)
        # 列名アクセス (row["company"]) を可能にする。後段の dict 組み立てが読みやすい。
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON;")

        if not self._tables_exist():
            self._apply_schema()

    # ------------------------------------------------------------------
    # 内部ヘルパ
    # ------------------------------------------------------------------
    def _tables_exist(self) -> bool:
        """中核テーブル (deals) が既に存在するかを返す。"""
        row = self.conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' AND name = 'deals';"
        ).fetchone()
        return row is not None

    def _apply_schema(self) -> None:
        """schema.sql を読み込んで一括適用する (テーブル未作成時のみ呼ばれる)。"""
        # encoding="utf-8" を明示。schema.sql に日本語コメントが含まれるため、
        # 実行環境の既定エンコーディングに依存させない (init_db.py と同方針)。
        schema_sql = SCHEMA_PATH.read_text(encoding="utf-8")
        # executescript は暗黙のうちに先行トランザクションをコミットしてから
        # 複数文を実行する。スキーマ適用 (DDL 一括) に最適。
        self.conn.executescript(schema_sql)

    # ------------------------------------------------------------------
    # 書き込み
    # ------------------------------------------------------------------
    def save_evaluation(
        self,
        deal_name: str,
        deal_amount: int,
        pipeline_result: dict,
        transcript: str = "",
        model: str = DEFAULT_MODEL,
    ) -> int:
        """pipeline 結果を 3 テーブルに 1 トランザクションで書き込む。

        run_pipeline() の戻り値 dict を受け取り、deals → evaluation_sessions →
        meddpicc_evaluations の順に INSERT する。3 テーブルへの書き込みは
        `with self.conn:` で 1 トランザクションにまとめ、途中で失敗したら
        全体をロールバックする (原子性の保証)。

        phase / revenue は派生値のため保存しない (get 時に再計算)。詳細は
        docs/specs/phase9_db_repository.md §4 の対応表を参照。

        Args:
            deal_name: 顧客企業名。deals.company に入る。
            deal_amount: 商談金額 (円)。deals.amount に入る。
            pipeline_result: run_pipeline() の戻り値。
                {"meddpicc_evaluations": dict, "phase": int, "revenue": dict}。
                本メソッドが使うのは meddpicc_evaluations のみ
                (phase / revenue は派生値なので保存しない)。
                meddpicc_evaluations の各値は {"status": str, "evidence": {...}}。
            transcript: 議事録原文。任意 (default "")。schema は NOT NULL だが
                空文字列で満たせる。pipeline 統合時 (Phase 11) に実値を渡す。
            model: 使用 LLM モデル名。任意 (default DEFAULT_MODEL)。

        Returns:
            新規作成された evaluation_sessions.id (= session_id)。

        Raises:
            KeyError: meddpicc_evaluations に未知のキーが含まれる場合
                (ITEM_CODE_MAP に無いキー)。extractor 破損の検出点。
            sqlite3.IntegrityError: status / item_code が CHECK 制約に違反する場合。
                schema.sql の CHECK が最終防壁 (軸 3、透過)。
        """
        meddpicc = pipeline_result["meddpicc_evaluations"]

        # 8 項目を (item_code, status, evidence_json) のタプル列に変換する。
        # evidence は dict なので JSON 文字列化して TEXT カラムに格納する
        # (ensure_ascii=False で日本語をそのまま保持し、後で読みやすくする)。
        # score 等の追加キーがあっても無視する (.get で防御的に扱う)。
        meddpicc_rows = []
        for key, body in meddpicc.items():
            item_code = ITEM_CODE_MAP[key]  # 未知キーは KeyError で透過
            status = body["status"]
            evidence = body.get("evidence")
            evidence_json = (
                json.dumps(evidence, ensure_ascii=False)
                if evidence is not None
                else None
            )
            meddpicc_rows.append((item_code, status, evidence_json))

        # `with self.conn:` は正常終了で commit、例外で rollback を自動で行う
        # (接続は閉じない)。3 テーブルの INSERT をこのブロックに収め原子性を担保。
        with self.conn:
            # deals: deal_date は schema に default が無いため SQL 側で当日日付を入れる。
            # outcome は default 'open' なので省略。
            cur = self.conn.execute(
                "INSERT INTO deals (company, amount, deal_date) "
                "VALUES (?, ?, DATE('now'));",
                (deal_name, deal_amount),
            )
            deal_id = cur.lastrowid

            # evaluation_sessions: evaluated_at / created_at は default で埋まる。
            cur = self.conn.execute(
                "INSERT INTO evaluation_sessions (deal_id, transcript, model) "
                "VALUES (?, ?, ?);",
                (deal_id, transcript, model),
            )
            session_id = cur.lastrowid

            # meddpicc_evaluations: 8 行を一括 INSERT。session_id を各行に前置する。
            self.conn.executemany(
                "INSERT INTO meddpicc_evaluations "
                "(session_id, item_code, status, evidence) VALUES (?, ?, ?, ?);",
                [(session_id, code, status, ev) for code, status, ev in meddpicc_rows],
            )

        return session_id

    # ------------------------------------------------------------------
    # 読み出し
    # ------------------------------------------------------------------
    def get_session(self, session_id: int) -> dict:
        """1 セッションを照会し、phase / revenue を再計算して返す。

        永続化済みの deal / session / meddpicc を読み出し、保存していない
        phase / revenue を judge_phase / calc_expected_revenue でその場で
        再計算して dict に含める (軸 1)。

        Args:
            session_id: evaluation_sessions.id。

        Returns:
            セッション dict (構造は docs/specs/phase9_db_repository.md §3 参照)。
            meddpicc_evaluations は MEDDPICC 正準順のネストキー dict で、各値は
            {"status": str, "evidence": dict | None}。phase (int) と revenue
            (calc_expected_revenue 戻り値 dict) は再計算値。

        Raises:
            ValueError: 該当 session_id が存在しない場合。
        """
        # session と紐づく deal を 1 クエリで結合取得する。
        row = self.conn.execute(
            "SELECT s.id AS session_id, s.transcript, s.model, s.evaluated_at, "
            "       d.id AS deal_id, d.company, d.amount, d.deal_date, "
            "       d.outcome, d.closed_at, d.actual_revenue, d.created_at "
            "FROM evaluation_sessions s "
            "JOIN deals d ON d.id = s.deal_id "
            "WHERE s.id = ?;",
            (session_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"session_id={session_id} が見つかりません")

        # meddpicc 明細 8 行を取得し、ネストキー → {status, evidence} に復元する。
        meddpicc_rows = self.conn.execute(
            "SELECT item_code, status, evidence FROM meddpicc_evaluations "
            "WHERE session_id = ?;",
            (session_id,),
        ).fetchall()

        meddpicc = self._rebuild_meddpicc(meddpicc_rows)

        # 派生値の再計算: flat な status dict を作って judge_phase / calc に渡す。
        flat = {key: body["status"] for key, body in meddpicc.items()}
        phase = judge_phase(flat)
        revenue = calc_expected_revenue(row["amount"], phase, flat)

        return {
            "session_id": row["session_id"],
            "deal": {
                "id": row["deal_id"],
                "company": row["company"],
                "amount": row["amount"],
                "deal_date": row["deal_date"],
                "outcome": row["outcome"],
                "closed_at": row["closed_at"],
                "actual_revenue": row["actual_revenue"],
                "created_at": row["created_at"],
            },
            "transcript": row["transcript"],
            "model": row["model"],
            "evaluated_at": row["evaluated_at"],
            "meddpicc_evaluations": meddpicc,
            "phase": phase,
            "revenue": revenue,
        }

    def list_sessions(self, limit: int = 20) -> list[dict]:
        """評価セッションを新しい順に最大 limit 件、サマリで返す。

        v1 要件「商談一覧ビュー (金額・確率・期待収益でソート可能)」(CLAUDE.md §7)
        のため、phase / probability / expected_revenue を再計算して各行に含める。
        N+1 を避けるため、対象 session の meddpicc 明細を 1 クエリでまとめて取得し
        session_id 毎に集約してから再計算する。

        Args:
            limit: 取得上限件数 (default 20)。

        Returns:
            サマリ dict のリスト (evaluated_at 降順)。各要素のキーは
            session_id / deal_id / company / amount / model / evaluated_at /
            phase / probability / expected_revenue。
        """
        # 1) session + deal を新しい順に limit 件取得。
        session_rows = self.conn.execute(
            "SELECT s.id AS session_id, s.model, s.evaluated_at, "
            "       d.id AS deal_id, d.company, d.amount "
            "FROM evaluation_sessions s "
            "JOIN deals d ON d.id = s.deal_id "
            "ORDER BY s.evaluated_at DESC, s.id DESC "
            "LIMIT ?;",
            (limit,),
        ).fetchall()
        if not session_rows:
            return []

        # 2) 対象 session の meddpicc 明細を 1 クエリでまとめて取得 (N+1 回避)。
        session_ids = [row["session_id"] for row in session_rows]
        placeholders = ", ".join("?" for _ in session_ids)
        meddpicc_rows = self.conn.execute(
            f"SELECT session_id, item_code, status, evidence "
            f"FROM meddpicc_evaluations WHERE session_id IN ({placeholders});",
            session_ids,
        ).fetchall()

        # session_id ごとに明細をグルーピングする。
        by_session: dict[int, list[sqlite3.Row]] = {}
        for row in meddpicc_rows:
            by_session.setdefault(row["session_id"], []).append(row)

        # 3) session 毎に phase / revenue を再計算してサマリを組み立てる。
        result = []
        for srow in session_rows:
            meddpicc = self._rebuild_meddpicc(by_session.get(srow["session_id"], []))
            flat = {key: body["status"] for key, body in meddpicc.items()}
            phase = judge_phase(flat)
            revenue = calc_expected_revenue(srow["amount"], phase, flat)
            result.append(
                {
                    "session_id": srow["session_id"],
                    "deal_id": srow["deal_id"],
                    "company": srow["company"],
                    "amount": srow["amount"],
                    "model": srow["model"],
                    "evaluated_at": srow["evaluated_at"],
                    "phase": phase,
                    "probability": revenue["probability"],
                    "expected_revenue": revenue["expected_revenue"],
                }
            )
        return result

    @staticmethod
    def _rebuild_meddpicc(rows: list) -> dict:
        """meddpicc 明細行 (item_code/status/evidence) をネストキー dict に復元する。

        item_code を CODE_TO_KEY で snake_case キーに戻し、evidence の JSON 文字列を
        dict に復元する。返却順は ITEM_CODE_MAP の定義順 (MEDDPICC 正準順) に揃える
        ことで、save 前 (extractor 戻り値) と同じ並びで round-trip 比較できる。

        Args:
            rows: sqlite3.Row のリスト (item_code / status / evidence を持つ)。

        Returns:
            {snake_case_key: {"status": str, "evidence": dict | None}} の dict。
        """
        # item_code → 行 の辞書をいったん作り、正準順で取り出す。
        by_code = {row["item_code"]: row for row in rows}
        rebuilt = {}
        for code, key in CODE_TO_KEY.items():
            row = by_code.get(code)
            if row is None:
                # 8 項目揃っていない (データ破損) ケース。スキップせず素直に欠落させ、
                # 後段の judge_phase がキー不一致を ValueError で検出できるようにする。
                continue
            evidence = json.loads(row["evidence"]) if row["evidence"] is not None else None
            rebuilt[key] = {"status": row["status"], "evidence": evidence}
        return rebuilt

    # ------------------------------------------------------------------
    # ライフサイクル
    # ------------------------------------------------------------------
    def close(self) -> None:
        """DB 接続を閉じる。"""
        self.conn.close()

    def __enter__(self) -> "MSORepository":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()


if __name__ == "__main__":
    # 動作確認: 既存サンプル JSON (Phase 2 / Phase 4) を pipeline_result 形に整形し、
    # save → get の round-trip と list_sessions を確認する。LLM は呼ばない。
    # 実行コマンド: python -m db.repository
    import json as _json
    from pprint import pprint

    project_root = Path(__file__).parent.parent
    examples_dir = project_root / "prompts" / "examples"
    test_db_path = project_root / "data" / "mso.db"
    DEAL = 10_000_000  # 1,000 万円 (基準値、pipeline.py / revenue.py と同値)

    print("Phase 9 MSORepository 動作確認")
    print("=" * 70)

    # テスト DB を作り直して決定論的な round-trip にする (append-only なので
    # 再実行で件数が増えるのを防ぐ。data/mso.db は .gitignore 対象のテスト用)。
    if test_db_path.exists():
        test_db_path.unlink()
        print(f"既存のテスト DB を削除: {test_db_path}")

    def _build_pipeline_result(json_filename: str) -> dict:
        """サンプル抽出 JSON を run_pipeline() 戻り値と同じ形に整形する。"""
        data = _json.loads((examples_dir / json_filename).read_text(encoding="utf-8"))
        flat = {k: v["status"] for k, v in data.items()}
        phase = judge_phase(flat)
        revenue = calc_expected_revenue(DEAL, phase, flat)
        return {"meddpicc_evaluations": data, "phase": phase, "revenue": revenue}

    with MSORepository(str(test_db_path)) as repo:
        cases = [
            ("sample_extraction_phase2.json", "サンプル製作所 P2", 2),
            ("sample_extraction_phase4.json", "サンプル製作所 P4", 4),
        ]
        saved_ids = []
        for filename, deal_name, expected_phase in cases:
            result = _build_pipeline_result(filename)
            sid = repo.save_evaluation(
                deal_name, DEAL, result, transcript="(サンプル議事録)", model="sample"
            )
            saved_ids.append((sid, result, expected_phase))
            print(f"\n[save] {filename} → session_id={sid} (deal='{deal_name}')")

        print("\n" + "-" * 70)
        print("round-trip 検証 (save → get):")
        for sid, original, expected_phase in saved_ids:
            got = repo.get_session(sid)
            # 1) phase 一致
            ok_phase = got["phase"] == expected_phase == original["phase"]
            # 2) 8 項目の status 一致
            orig_status = {
                k: v["status"] for k, v in original["meddpicc_evaluations"].items()
            }
            got_status = {
                k: v["status"] for k, v in got["meddpicc_evaluations"].items()
            }
            ok_status = orig_status == got_status
            # 3) evidence 一致 (JSON シリアライズ → 復元のラウンドトリップ)
            orig_ev = {
                k: v.get("evidence")
                for k, v in original["meddpicc_evaluations"].items()
            }
            got_ev = {
                k: v["evidence"] for k, v in got["meddpicc_evaluations"].items()
            }
            ok_ev = orig_ev == got_ev
            # 4) revenue 再計算一致
            ok_rev = (
                got["revenue"]["expected_revenue"]
                == original["revenue"]["expected_revenue"]
            )
            verdict = "OK" if (ok_phase and ok_status and ok_ev and ok_rev) else "NG"
            print(
                f"  [{verdict}] session_id={sid}: phase={got['phase']} "
                f"(期待 {expected_phase})  "
                f"status一致={ok_status}  evidence一致={ok_ev}  "
                f"expected_revenue={got['revenue']['expected_revenue']:,} 円"
            )

        print("\n" + "-" * 70)
        print("list_sessions() (新しい順):")
        for s in repo.list_sessions():
            print(
                f"  session_id={s['session_id']}  '{s['company']}'  "
                f"phase={s['phase']}  prob={s['probability']:.5f}  "
                f"expected={s['expected_revenue']:,} 円  ({s['evaluated_at']})"
            )

        print("\n" + "-" * 70)
        print("get_session の全体構造サンプル (最初の session):")
        pprint(repo.get_session(saved_ids[0][0]), sort_dicts=False, width=100)

    print(f"\nDB ファイル生成確認: {test_db_path} -> {test_db_path.exists()}")
    print("=" * 70)
    print("動作確認終わり")
