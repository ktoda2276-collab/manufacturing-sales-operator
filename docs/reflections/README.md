# docs/reflections/

このディレクトリは、Manufacturing Sales Operator (MSO) プロジェクトの日次振り返り（retrospective）を蓄積する場所です。

## 目的

- **進捗の見える化**: 3スケール（MSO全体 / v1開発 / 転職活動）での進捗を毎日記録
- **設計判断の履歴**: 「いつ・なぜ・何を決めたか」を遡及可能にする
- **面接準備の素材**: 日々の学びと判断の蓄積を、面接で語れる形で保存

## 振り返りの3スケール

- **スケール1**: MSO ポートフォリオ全体（12週間ロードマップ、v1〜v4）
- **スケール2**: v1 (MEDDPICC Analysis Engine) の開発（21日想定）
- **スケール3**: 転職活動全体（3ヶ月計画、軸A/B/C で並走）

## ファイル命名規則

```
day_NN_YYYY-MM-DD.md
```

例:

- `day_01_2026-05-09.md`
- `day_02_2026-05-10.md`
- `day_05_2026-05-12.md`

## 運用フロー

1. Day N 終了時、claude.ai で振り返りテンプレ（NEXT_SESSION.md §7 参照）を埋める
2. 埋まったものは NEXT_SESSION.md §2「直近3日分の振り返り」にスライドイン
3. 4日前のものは `docs/reflections/day_NN_YYYY-MM-DD.md` にアーカイブ
4. アーカイブされたファイルは git にコミットして履歴に残す

## NEXT_SESSION.md との関係

| ファイル | 役割 | git 管理 |
|---|---|---|
| `NEXT_SESSION.md`（リポジトリルート） | 直近3日分 + 進捗ダッシュボード | 管理外 |
| `docs/reflections/day_NN_*.md` | 4日前以前のアーカイブ | 管理内 |

NEXT_SESSION.md は「次回セッション開始時の入り口メモ」として軽量に保ち、過去ログは本ディレクトリで永続化する。

---

*Last updated: 2026-05-11 (Day 4 / D-90)*
