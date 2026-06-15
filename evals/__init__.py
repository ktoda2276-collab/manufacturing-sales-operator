"""評価フレームワーク パッケージ (Phase 10)

ケース台帳 (evals/dataset/cases.json) とゴールド JSON のロードを担う薄い
ヘルパーをここに置く。test_meddpicc.py (箱の検証) と run_eval.py (実評価) の
双方が参照するため、パッケージ初期化モジュールを共有の入り口にする。

パスはすべてプロジェクトルート基準で解決する (cases.json には
"prompts/examples/..." のようなルート相対パスを書く約束)。
"""
import json
from pathlib import Path

# evals/ の 1 つ上 = プロジェクトルート。
PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent

# ケース台帳の場所 (evals/dataset/cases.json)。
CASES_PATH: Path = Path(__file__).resolve().parent / "dataset" / "cases.json"


def load_cases() -> list[dict]:
    """ケース台帳 (cases.json) を読み込んで list[dict] で返す。

    各要素: {"name", "transcript_path", "gold_path", "expected_phase"}。
    パスはルート相対の文字列のまま (解決は load_gold / load_transcript で行う)。
    """
    return json.loads(CASES_PATH.read_text(encoding="utf-8"))


def load_gold(case: dict) -> dict:
    """ケースの gold_path を解決し、正解 MEDDPICC dict を返す。"""
    return json.loads(
        (PROJECT_ROOT / case["gold_path"]).read_text(encoding="utf-8")
    )


def load_transcript(case: dict) -> str:
    """ケースの transcript_path を解決し、議事録テキストを返す。"""
    return (PROJECT_ROOT / case["transcript_path"]).read_text(encoding="utf-8")
