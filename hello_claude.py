"""
hello_claude.py
----------------
Anthropic API への接続確認用の最小スクリプト。
Claude Haiku 4.5 に挨拶を送り、返答とトークン使用量を表示する。
"""

# os: 環境変数（OS が持っている設定値）にアクセスするための標準ライブラリ
import os

# dotenv: .env ファイルに書いた KEY=VALUE を環境変数として読み込んでくれるライブラリ
# load_dotenv() を呼ぶと、同じディレクトリにある .env を自動で探して読み込む
from dotenv import load_dotenv

# anthropic: Anthropic 公式の Python SDK。Claude API を呼ぶための薄いラッパー
from anthropic import Anthropic


def main() -> None:
    # --- 1) .env から API キーを読み込む ----------------------------------
    # load_dotenv() は .env を読み込み、中の値を os.environ に注入する
    # 戻り値は「.env が見つかったか」の bool。デバッグ用に変数で受けておく
    loaded = load_dotenv()

    # os.environ.get("KEY") は、環境変数が無ければ None を返す（KeyError にならない）
    api_key = os.environ.get("ANTHROPIC_API_KEY")

    # API キーが空 or 未設定なら早期に止める。これをやらないと、
    # この後 SDK 初期化で分かりにくいエラーが出ることがある
    if not api_key:
        # raise: 例外を発生させてプログラムを止める。ここでは値が無いので RuntimeError
        raise RuntimeError(
            ".env から ANTHROPIC_API_KEY を読み込めませんでした。"
            f"(load_dotenv の結果: {loaded})"
        )

    # --- 2) Claude クライアントを初期化 -----------------------------------
    # Anthropic(api_key=...) で API クライアントを作る
    # 引数を省略すると環境変数 ANTHROPIC_API_KEY が自動で参照されるが、
    # 学習目的のため明示的に渡している
    client = Anthropic(api_key=api_key)

    # --- 3) メッセージを送信 ----------------------------------------------
    # client.messages.create(...) が Claude API への HTTP リクエストを実行する
    # - model: 使用するモデルの ID（Haiku は最速・低コスト）
    # - max_tokens: 出力の最大トークン数（多すぎる暴走を防ぐ安全弁）
    # - messages: 会話履歴。role は "user" or "assistant"。今回は最初の発話だけ
    response = client.messages.create(
        model="claude-haiku-4-5",
        max_tokens=200,
        messages=[
            {
                "role": "user",
                "content": "こんにちは、自己紹介してください",
            }
        ],
    )

    # --- 4) レスポンス本文を表示 ------------------------------------------
    # response.content は「ブロックの配列」。テキスト応答なら TextBlock が並ぶ
    # 各ブロックの .text を連結すれば、表示用の本文になる
    # （ツール使用などの応答だと TextBlock 以外も混じるが、今回は text のみ想定）
    answer_text = "".join(
        block.text for block in response.content if block.type == "text"
    )

    print("=" * 60)
    print("Claude からの返答:")
    print("=" * 60)
    print(answer_text)

    # --- 5) トークン使用量を表示 ------------------------------------------
    # response.usage に入力/出力トークン数が入っている
    # input_tokens : こちらが送った内容のトークン数（課金対象）
    # output_tokens: Claude が返した内容のトークン数（課金対象）
    usage = response.usage
    print()
    print("=" * 60)
    print("トークン使用量:")
    print("=" * 60)
    print(f"  input_tokens : {usage.input_tokens}")
    print(f"  output_tokens: {usage.output_tokens}")
    print(f"  合計          : {usage.input_tokens + usage.output_tokens}")


# Python の慣用句: このファイルを直接実行したときだけ main() を呼ぶ
# （他のファイルから import されたときは実行されない）
if __name__ == "__main__":
    main()
