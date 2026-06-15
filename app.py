"""MSO v1 — Streamlit UI エントリポイント (Phase 11)

商談一覧と評価詳細を**読み取り専用**で表示するブラウザ UI。CLAUDE.md §7 の
v1 要件のうち「商談一覧ビュー（ソート可能）」と「不足項目ハイライト + 次アクション
提案」を満たす。

設計判断 (詳細は docs/specs/phase11_streamlit_ui.md):
- 軸 1 (可逆): UI は表示のみ。DB を書き換えない (save_evaluation を呼ばない)。
  新規評価 (LLM 課金あり) は本 Phase のスコープ外。
- 軸 2 (判断は LLM、計算は Python): フェーズ判定・期待収益・不足項目分析は
  すべて core/ の純関数が算出。app.py は MSORepository から取得して
  ウィジェットに流すだけで、業務ロジックを再実装しない。
- DB アクセス: 再描画ごとに短命接続を開く (`with MSORepository(...)`)。
  SQLite ローカル接続は安価で、Streamlit の再実行スレッドと sqlite3 接続の
  相性問題 (check_same_thread) を接続を持ち越さないことで回避する。

実行方法:
    streamlit run app.py

デモデータ投入 (LLM 不使用、Phase 2/4 サンプル 2 件):
    python -m db.repository
"""
import os

import streamlit as st

from core.phase import ITEM_LABELS, analyze_gaps
from db.repository import MSORepository

# DB パス。環境変数 MSO_DB_PATH で上書き可能 (既定は db.repository の動作確認と同じ)。
# data/ は .gitignore 対象のローカル専用ディレクトリ。
DB_PATH = os.environ.get("MSO_DB_PATH", "data/mso.db")

# status → 表示バッジ。色付き絵文字で一目で危険信号 (risk) が分かるようにする。
STATUS_BADGE = {
    "confirmed": "🟢 confirmed",
    "partial": "🟡 partial",
    "unconfirmed": "⚪ unconfirmed",
    "risk": "🔴 risk",
}

# MEDDPICC 8 項目の正準表示順 (ITEM_LABELS の定義順 = M/E/D1/D2/P/I/C1/C2 相当)。
MEDDPICC_ORDER = list(ITEM_LABELS.keys())


# ---------------------------------------------------------------------------
# データ取得 (読み取り専用、短命接続)
# ---------------------------------------------------------------------------
def load_sessions(limit: int = 50) -> list[dict]:
    """評価セッション一覧サマリを新しい順に取得する。"""
    with MSORepository(DB_PATH) as repo:
        return repo.list_sessions(limit=limit)


def load_session(session_id: int) -> dict:
    """1 セッションの詳細 (deal / meddpicc / phase / revenue) を取得する。"""
    with MSORepository(DB_PATH) as repo:
        return repo.get_session(session_id)


# ---------------------------------------------------------------------------
# 表示パーツ
# ---------------------------------------------------------------------------
def render_list(sessions: list[dict]) -> None:
    """商談一覧を表で描画する。ヘッダクリックで金額・確率・期待収益でソートできる。"""
    st.subheader("商談一覧")

    # list_sessions の dict を、日本語見出しの表示用 dict に並べ替える。
    # probability は %、金額・期待収益は円で見やすく整形する。
    table = [
        {
            "会社": s["company"],
            "Phase": s["phase"],
            "確率(%)": round(s["probability"] * 100, 1),
            "期待収益(円)": s["expected_revenue"],
            "金額(円)": s["amount"],
            "モデル": s["model"],
            "評価日時": s["evaluated_at"],
            "session_id": s["session_id"],
        }
        for s in sessions
    ]
    # st.dataframe は列ヘッダクリックで対話的にソートできる (要件を標準機能で充足)。
    st.dataframe(table, width="stretch", hide_index=True)


def render_gaps(gaps: dict) -> None:
    """不足項目ハイライト + 次アクション提案 + リスク項目を描画する。"""
    st.markdown("#### 🔎 不足項目と次アクション")

    next_phase = gaps["next_phase"]
    missing = gaps["missing_items"]

    if next_phase is None:
        st.success("MEDDPICC 8 項目すべてが confirmed です（最終フェーズ Phase 5 到達）。")
    elif not missing:
        # 理論上ここには来にくいが (missing 空なら次フェーズに到達しているはず)、
        # 防御的に分岐を用意しておく。
        st.info(f"次フェーズ Phase {next_phase} に必要な必須項目はすべて confirmed です。")
    else:
        st.warning(
            f"現在 Phase {gaps['current_phase']} → 次の Phase {next_phase} に進むには、"
            f"以下 {len(missing)} 項目を confirmed にする必要があります。"
        )
        for item in missing:
            st.markdown(
                f"- **{item['label']}** "
                f"（現状: {STATUS_BADGE.get(item['status'], item['status'])}）  \n"
                f"  → {item['action']}"
            )

    # リスク項目はフェーズ前進と独立に常に警告表示する (危険信号)。
    if gaps["risk_items"]:
        st.error(
            "⚠️ リスク項目（status=risk）: "
            + "、".join(r["label"] for r in gaps["risk_items"])
        )


def render_meddpicc(meddpicc: dict) -> None:
    """MEDDPICC 8 項目の status と evidence を描画する。"""
    st.markdown("#### 📋 MEDDPICC 8 項目")
    for key in MEDDPICC_ORDER:
        body = meddpicc.get(key)
        if body is None:
            continue  # データ破損時はスキップ (8 項目揃わないケース)
        status = body["status"]
        evidence = body.get("evidence")
        label = ITEM_LABELS[key]
        badge = STATUS_BADGE.get(status, status)

        # 各項目を expander にし、status を見出し、evidence を中身にする。
        with st.expander(f"{badge} ｜ {label}", expanded=False):
            if not evidence:
                st.caption("根拠（evidence）は記録されていません。")
            elif isinstance(evidence, dict):
                # quote / speaker / interpretation を想定。未知キーも素直に出す。
                quote = evidence.get("quote")
                speaker = evidence.get("speaker")
                interpretation = evidence.get("interpretation")
                if quote:
                    speaker_label = f"（{speaker}）" if speaker else ""
                    st.markdown(f"> {quote} {speaker_label}")
                if interpretation:
                    st.markdown(f"**解釈:** {interpretation}")
                # 上記以外のキーがあれば JSON で補足表示する。
                extra = {
                    k: v
                    for k, v in evidence.items()
                    if k not in ("quote", "speaker", "interpretation")
                }
                if extra:
                    st.json(extra)
            else:
                # dict 以外 (想定外) はそのまま表示する。
                st.write(evidence)


def render_detail(session_id: int) -> None:
    """選択された 1 セッションの詳細ビューを描画する。"""
    session = load_session(session_id)
    deal = session["deal"]
    revenue = session["revenue"]
    meddpicc = session["meddpicc_evaluations"]

    st.subheader(f"詳細: {deal['company']}")

    # 主要指標を 4 メトリクスで一望できるようにする。
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Phase", f"{session['phase']} / 5")
    col2.metric("確率", f"{revenue['probability'] * 100:.1f}%")
    col3.metric("期待収益", f"{revenue['expected_revenue']:,} 円")
    col4.metric("商談金額", f"{deal['amount']:,} 円")

    # flat な status dict を作って不足項目分析にかける (UI 側でロジックは持たない)。
    flat = {key: body["status"] for key, body in meddpicc.items()}
    gaps = analyze_gaps(flat)
    render_gaps(gaps)

    st.divider()
    render_meddpicc(meddpicc)


# ---------------------------------------------------------------------------
# メイン
# ---------------------------------------------------------------------------
def main() -> None:
    st.set_page_config(page_title="MSO — MEDDPICC 評価ビューア", layout="wide")
    st.title("📊 Manufacturing Sales Operator — MEDDPICC 評価ビューア")
    st.caption(
        "商談文字起こしから抽出した MEDDPICC 評価・フェーズ・期待収益を閲覧します"
        "（読み取り専用 / Phase 11）。"
    )

    sessions = load_sessions()

    if not sessions:
        # 空 DB のときは投入手順を案内する (クラッシュさせない)。
        st.info(
            f"評価データがまだありません（DB: `{DB_PATH}`）。\n\n"
            "デモデータを投入するには、ターミナルで次を実行してください（LLM 不使用）:\n"
            "```\npython -m db.repository\n```"
        )
        return

    render_list(sessions)

    st.divider()

    # 詳細を見るセッションを選ぶ。会社名・Phase・session_id をラベルにする。
    options = {
        f"{s['company']}（Phase {s['phase']}） ｜ session #{s['session_id']}": s[
            "session_id"
        ]
        for s in sessions
    }
    selected_label = st.selectbox("詳細を表示する商談を選択", list(options.keys()))
    if selected_label:
        render_detail(options[selected_label])


if __name__ == "__main__":
    main()
