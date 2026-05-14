"""MEDDPICC 抽出プロンプト組み立てモジュール (Phase 5)

商談議事録を入力として、Claude API に渡す MEDDPICC 抽出プロンプト文字列を組み立てる
純粋関数を提供する。

設計判断:
- 軸 1 (YAGNI): Few-shot ファイルパスはモジュール定数としてハードコード。
  引数化・キャッシュ・遅延読み込みなどの最適化は今は不要。後から追加できる判断は YAGNI。
- 軸 2 (判断は LLM、計算は Python): プロンプト生成は決定論的 Python の責務。
  LLM が担うのは status 判定と evidence 抽出のみ。
- 軸 3 (Defense in Depth): XML タグでデータと指示を物理的に分離する層を担当する。
  入力検証 (議事録最小長) は呼び出し側 (Phase 6 core/extractor.py) で行う。

参照:
- prompts/meddpicc_extraction.md: MEDDPICC 抽出プロンプト本体の設計仕様 (Day 3)
- prompts/examples/SPEC.md §6: 出力 JSON スキーマの確定仕様 (Day 5)
- prompts/examples/sample_transcript_phase{2,4}.md: Few-shot 例の議事録 (Day 6)
- prompts/examples/sample_extraction_phase{2,4}.json: Few-shot 例の期待出力 (Day 6)
"""
from pathlib import Path

# Few-shot 例のファイルパス
# __file__ 基準で解決するため、呼び出し側の cwd に依存しない
_EXAMPLES_DIR = Path(__file__).parent / "examples"
PHASE2_TRANSCRIPT_PATH = _EXAMPLES_DIR / "sample_transcript_phase2.md"
PHASE2_EXTRACTION_PATH = _EXAMPLES_DIR / "sample_extraction_phase2.json"
PHASE4_TRANSCRIPT_PATH = _EXAMPLES_DIR / "sample_transcript_phase4.md"
PHASE4_EXTRACTION_PATH = _EXAMPLES_DIR / "sample_extraction_phase4.json"


def build_extraction_prompt(transcript: str) -> str:
    """商談議事録を入力として、MEDDPICC 抽出用の完全なプロンプト文字列を返す。

    Args:
        transcript: 商談議事録 (録音文字起こし)。
            話者ラベルは「苗字 役職(顧客/自社):」形式を想定 (SPEC.md §7)。
            議事録の最小長チェックは本関数では行わない (Phase 6 で実施)。

    Returns:
        Claude API に渡す user prompt 文字列。以下の 5 つの XML タグで構造化される:
        <task> / <schema> / <examples> / <instructions> / <input>

    Note:
        本関数は純粋関数: 同じ transcript からは常に同じプロンプトを返す
        (Few-shot ファイルが変わらない限り)。
        実際の Claude API コールは Phase 6 (core/extractor.py) で実装する。
    """
    # Few-shot 例の中身をファイルから読み込む
    # 軸 1 (YAGNI): キャッシュは今は不要。呼び出し都度読み込む方が
    # Few-shot 更新時のホットリロード性が高く、副作用も小さい。
    phase2_transcript = PHASE2_TRANSCRIPT_PATH.read_text(encoding="utf-8")
    phase2_extraction = PHASE2_EXTRACTION_PATH.read_text(encoding="utf-8")
    phase4_transcript = PHASE4_TRANSCRIPT_PATH.read_text(encoding="utf-8")
    phase4_extraction = PHASE4_EXTRACTION_PATH.read_text(encoding="utf-8")

    # f-string 内では JSON の `{` `}` は `{{` `}}` でエスケープする。
    # 変数展開は phase2/4_transcript, phase2/4_extraction, transcript の 5 箇所のみ。
    return f"""<task>
あなたは製造業 BtoB 営業の MEDDPICC アナリストです。
以下の商談議事録 (録音文字起こし) から、MEDDPICC 8 項目それぞれについて、
現時点での状態 (status) と、その判断の根拠となる顧客側発話 (evidence) を抽出してください。
顧客側の発話を予測の根拠の主軸とし、自社 (営業) 側の発話は補助情報として扱ってください。
出力は <schema> で定義された JSON 形式のみとし、前置き・後置き・Markdown コードフェンスは
一切含めないでください。
</task>

<schema>
出力構造の例 (値は実際の判定結果で埋めること):

{{
  "metrics":           {{ "status": "...", "evidence": {{ "quote": "...", "speaker": "...", "interpretation": "..." }} }},
  "economic_buyer":    {{ "status": "...", "evidence": {{ ... }} }},
  "decision_criteria": {{ "status": "...", "evidence": {{ ... }} }},
  "decision_process":  {{ "status": "...", "evidence": {{ ... }} }},
  "paper_process":     {{ "status": "...", "evidence": {{ ... }} }},
  "identify_pain":     {{ "status": "...", "evidence": {{ ... }} }},
  "champion":          {{ "status": "...", "evidence": {{ ... }} }},
  "competition":       {{ "status": "...", "evidence": {{ ... }} }}
}}

出力はフラットな 8 項目並列構造の JSON とする。8 項目すべてを必ず含めること。

ルートキー (固定、この順序で出力すること):
  metrics, economic_buyer, decision_criteria, decision_process,
  paper_process, identify_pain, champion, competition

各項目の値は {{ "status": ..., "evidence": ... }} の構造を持つオブジェクト。

status enum (以下 4 値のいずれか):
  - "confirmed"   : 議事録内の発話で十分な根拠がある
  - "partial"     : 部分的に確認できるが、完全な根拠ではない
  - "unconfirmed" : 議事録内で言及されていない、または根拠が不十分
  - "risk"        : 確認されているが、リスク要因が明示されている

evidence (3 フィールドのオブジェクト):
  - "quote"          : 発話の引用文字列 (クリーンアップ済み)
  - "speaker"        : 話者ラベル (例: "田中 部長(顧客)")
  - "interpretation" : その発話が当該 status の根拠となる理由の簡潔な説明

unconfirmed の場合の evidence:
  関連発話が議事録内に存在しない場合は quote と speaker を空文字 "" とし、
  interpretation で「議事録内で当該項目に関する言及がない」旨を述べる。
  関連発話はあるが根拠不十分な場合は、その発話を quote/speaker に記述し、
  interpretation で「示唆はあるが確定していない」旨を述べる。
</schema>

<examples>
  <example>
    <transcript>
{phase2_transcript}
    </transcript>
    <extraction>
{phase2_extraction}
    </extraction>
  </example>
  <example>
    <transcript>
{phase4_transcript}
    </transcript>
    <extraction>
{phase4_extraction}
    </extraction>
  </example>
</examples>

<instructions>
以下を厳守してください。

1. quote は議事録から引用すること。議事録内に存在しない発話を生成してはならない
   (ハルシネーション禁止)。

2. quote は次の方針でクリーンアップした形で記述する:
   - フィラー (「えーと」「あの」「なんていうか」等) を除去
   - 言い淀み (言い直し) は最終形を採用
   - 重複発話は最も明確な言い回しを採用
   - 句読点を整える
   - 内容そのものは変えない (言い換え・要約はしない)

3. 該当発話が議事録内に見つからない項目は、status を "unconfirmed" とし、
   evidence は <schema> の unconfirmed パターンに従って記述する。

4. 出力は <schema> 定義の JSON オブジェクトのみ。前置き・後置き・コメント・
   Markdown コードフェンス (```) を一切含めない。

5. JSON は valid な構文であること:
   - 文字列はダブルクォート
   - 末尾カンマ禁止
   - 8 項目すべて存在 (欠落禁止)
</instructions>

<input>
{transcript}
</input>
"""


if __name__ == "__main__":
    # 動作確認: ダミー transcript を渡してプロンプト全体を出力する。
    # 実運用での呼び出しは Phase 6 (core/extractor.py) で実装する。
    dummy_transcript = "テスト用商談議事録"
    prompt = build_extraction_prompt(dummy_transcript)
    print(prompt)
    print("---")
    print(f"プロンプト総文字数: {len(prompt):,} 文字")
