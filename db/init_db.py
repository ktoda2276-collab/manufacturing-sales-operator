"""
db/init_db.py
-------------
SQLite データベース `mso.db` を初期化するスクリプト。

役割:
    - `db/schema.sql` を読み込み、プロジェクトルートに `mso.db` を作成する
    - 既存の `mso.db` がある場合は、誤って上書きしないようユーザー確認を挟む
    - 完了時に作成されたテーブル一覧を表示し、適用結果を可視化する

実行方法:
    cd /Users/keiichirotoda/projects/manufacturing-sales-operator
    source venv/bin/activate
    python db/init_db.py

このスクリプトは「スキーマ適用専用」であり、テストデータの投入や
マイグレーションは扱わない（責務の分離）。
"""

import sqlite3
import sys
from pathlib import Path


# プロジェクトルートを基準にした絶対パス。
# Path(__file__) はこのファイル自身、.parent.parent で db/ の 1 つ上に上がる。
# 相対パスを使うと「どこから実行したか」で解決先が変わって事故るため、
# 絶対パスで固定するのが安全。
PROJECT_ROOT: Path = Path(__file__).parent.parent
SCHEMA_PATH: Path = PROJECT_ROOT / "db" / "schema.sql"
DB_PATH: Path = PROJECT_ROOT / "mso.db"


def confirm_overwrite(db_path: Path) -> bool:
    """
    既存の DB ファイルを上書きしてよいかをユーザーに対話で確認する。

    Returns:
        True なら処理続行（削除して作り直し）、False なら中断。
    """
    print(f"⚠️  既に DB ファイルが存在します: {db_path}")
    print("   このまま実行すると、既存データを破棄して再作成します。")
    answer = input("   続行しますか？ [y/N]: ").strip().lower()
    return answer in {"y", "yes"}


def read_schema(schema_path: Path) -> str:
    """
    schema.sql の内容を文字列として読み込む。

    ファイルが見つからない場合は FileNotFoundError をそのまま伝播させ、
    呼び出し側でユーザー向けメッセージに整形する。
    """
    # encoding="utf-8" を明示。日本語コメントが含まれるため、
    # システム既定エンコーディング依存にしないことで実行環境差を吸収する。
    return schema_path.read_text(encoding="utf-8")


def apply_schema(db_path: Path, schema_sql: str) -> list[str]:
    """
    指定の DB ファイルにスキーマを適用し、作成されたテーブル名一覧を返す。

    Args:
        db_path:    作成先の SQLite ファイルパス。存在しなければ自動生成される。
        schema_sql: 適用する DDL（CREATE TABLE 等）の SQL 文字列。

    Returns:
        sqlite_master を引いて取得した user テーブル名のリスト。

    Notes:
        - executescript() は複数の SQL 文を一括実行できる。スキーマ適用に最適。
        - with sqlite3.connect(...) ブロックを使うと、例外時のロールバックと
          正常終了時のコミットを自動で扱ってくれる（コンテキストマネージャ）。
    """
    with sqlite3.connect(db_path) as conn:
        # スキーマ側にも PRAGMA foreign_keys = ON; を入れているが、
        # この接続でも明示的に有効化しておく。Python 側の二重保険。
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.executescript(schema_sql)

        # sqlite_master からユーザー定義テーブルだけを抽出する。
        # SQLite 内部用の sqlite_* テーブルは除外。
        cursor = conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' AND name NOT LIKE 'sqlite_%' "
            "ORDER BY name;"
        )
        # cursor は (name,) のタプルを返すので、最初の要素を取り出してフラット化。
        return [row[0] for row in cursor.fetchall()]


def main() -> int:
    """
    エントリポイント。終了コードを返す（0=成功、非 0=失敗）。
    """
    # --- 1) schema.sql の存在確認 ---------------------------------------
    if not SCHEMA_PATH.exists():
        print(f"❌ スキーマファイルが見つかりません: {SCHEMA_PATH}", file=sys.stderr)
        return 1

    # --- 2) 既存 DB の上書き確認 ----------------------------------------
    if DB_PATH.exists():
        if not confirm_overwrite(DB_PATH):
            print("中断しました。既存の DB は変更されていません。")
            return 0
        # 既存ファイルを削除してから作り直す。これをやらないと CREATE TABLE が
        # 「table already exists」で失敗する（IF NOT EXISTS を付けない方針なので）。
        DB_PATH.unlink()
        print(f"既存の {DB_PATH.name} を削除しました。")

    # --- 3) スキーマ読み込みと適用 --------------------------------------
    try:
        schema_sql = read_schema(SCHEMA_PATH)
        tables = apply_schema(DB_PATH, schema_sql)
    except sqlite3.Error as e:
        # SQL 実行系のエラー（CHECK 制約違反、構文エラー、型不一致など）。
        # ユーザーには原因をそのまま見せる。
        print(f"❌ SQL 実行エラー: {e}", file=sys.stderr)
        return 2
    except OSError as e:
        # ファイル I/O 系のエラー（権限、ディスク容量など）。
        print(f"❌ ファイル I/O エラー: {e}", file=sys.stderr)
        return 3

    # --- 4) 結果表示 -----------------------------------------------------
    print("✅ テーブル作成完了")
    print(f"   DB ファイル: {DB_PATH}")
    print(f"   作成されたテーブル ({len(tables)} 件):")
    for name in tables:
        print(f"     - {name}")
    return 0


# このファイルを `python db/init_db.py` として直接実行したときだけ main() を呼ぶ。
# 他のモジュールから import される場合（テスト等）は副作用を起こさない。
if __name__ == "__main__":
    sys.exit(main())
