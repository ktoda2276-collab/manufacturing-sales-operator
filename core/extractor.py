"""MEDDPICC 抽出本体モジュール (Phase 6)

Phase 5 で実装した build_extraction_prompt() を使って、Claude API を実際に呼び、
MEDDPICC 抽出結果の dict を返す最小実装。

設計判断:
- 軸 1 (YAGNI): リトライ・キャッシュ・ストリーミング・構造化出力 API・
  Pydantic などの最適化や型強化は今は不要。
- 軸 2 (判断は LLM、計算は Python): status 判定と evidence 抽出は LLM、
  JSON パースと dict 返却は Python の責務。
- prefill (meddpicc_extraction.md §6.2) は v1 では採用しない。
  理由: (a) YAGNI、(b) Opus 4.7 の素の挙動を Phase 10 評価フレームワークの
  比較ベースラインにするため。
- temperature / top_p / top_k は Opus 4.7 で 400 エラーになる仕様のため
  指定しない (省略 = SDK デフォルト)。

参照:
- prompts/meddpicc_extraction_prompt.py: build_extraction_prompt() 本体 (Phase 5)
- prompts/meddpicc_extraction.md §7: モデル選定の根拠
- prompts/examples/SPEC.md §6: 出力 JSON スキーマ仕様
"""
import json
import os

from anthropic import Anthropic
from dotenv import load_dotenv

from prompts.meddpicc_extraction_prompt import build_extraction_prompt

# モジュールロード時に .env を読み込む。
# core/extractor.py はライブラリモジュール (Phase 7 や Streamlit から import される
# 想定) なので、関数呼び出し毎ではなくモジュールトップで 1 回読む方が自然。
# hello_claude.py は main() 内で呼んでいるが、あちらはスクリプト用途で役割が異なる。
load_dotenv()

# 使用モデル: Claude Opus 4.7。
# Anthropic 公式 Models API で確認済みの API 参照名 (alias ではなく実 ID)。
MODEL = "claude-opus-4-7"

# JSON 出力の最大トークン数。
# MEDDPICC 8 項目 + evidence (3 フィールド) は実測で 1k tokens 前後だが、
# interpretation が長くなるケースを想定して余裕を持たせる。
MAX_TOKENS = 4096


def extract_meddpicc(transcript: str) -> dict:
    """商談議事録を入力として、MEDDPICC 抽出結果の dict を返す。

    Args:
        transcript: 商談議事録 (録音文字起こし)。
            話者ラベルは「苗字 役職(顧客/自社):」形式を想定 (SPEC.md §7)。
            最小長チェックは本関数では行わない (Phase 10 評価で扱う)。

    Returns:
        MEDDPICC 8 項目の抽出結果を含む dict。SPEC.md §6 のフラット 8 項目並列
        構造 (metrics / economic_buyer / decision_criteria / decision_process /
        paper_process / identify_pain / champion / competition)。
        各値は {"status": str, "evidence": {...}} の構造。

    Raises:
        RuntimeError: ANTHROPIC_API_KEY が未設定の場合。
        anthropic.APIError 系: API 呼び出しエラー時 (Phase 10 で堅牢化、軸 1)。
        json.JSONDecodeError: 応答が valid JSON でない場合 (Phase 10 で堅牢化)。
    """
    # 設定の sanity check のみ実施。API エラーや JSON パースエラーは捕捉せず
    # 素通しする (Phase 10 評価フレームワークで発見・対応する設計)。
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError(
            "ANTHROPIC_API_KEY が未設定です。.env を確認してください。"
        )

    prompt = build_extraction_prompt(transcript)

    client = Anthropic()
    response = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        messages=[{"role": "user", "content": prompt}],
        # temperature / top_p / top_k は指定しない (Opus 4.7 の仕様)。
    )

    # 応答の content は ContentBlock のリスト。テキスト応答は通常 1 件目に入る。
    # 軸 1 (YAGNI): 複数 block / tool_use 等の処理は今のスコープ外。
    raw_text = response.content[0].text

    # Phase 5 の <instructions> で「JSON のみ出力、コードフェンスなし」を指示済み。
    # 万一フェンスや前置きが付いた場合は素通しで JSONDecodeError を投げる。
    return json.loads(raw_text)


if __name__ == "__main__":
    # 動作確認: Phase 2 議事録を実 API に投げて結果を観察する。
    # 期待される status 分布 (SPEC.md §4.3):
    #   confirmed 2 (identify_pain, champion)
    #   partial 1   (metrics)
    #   unconfirmed 5 (economic_buyer, decision_criteria, decision_process,
    #                  paper_process, competition)
    # LLM の確率的挙動により多少のズレは許容する (評価フレームワークは Phase 10)。
    from pathlib import Path
    from pprint import pprint

    sample_path = (
        Path(__file__).parent.parent
        / "prompts"
        / "examples"
        / "sample_transcript_phase2.md"
    )
    transcript = sample_path.read_text(encoding="utf-8")

    print(f"Input: {sample_path.name} ({len(transcript):,} 文字)")
    print(f"Model: {MODEL}, max_tokens: {MAX_TOKENS}")
    print("---")

    result = extract_meddpicc(transcript)

    print("Status 分布 (期待値: confirmed 2 / partial 1 / unconfirmed 5):")
    for item, body in result.items():
        print(f"  {item:20s}: {body['status']}")
    print("---")
    print("Full result:")
    pprint(result, sort_dicts=False, width=100)
