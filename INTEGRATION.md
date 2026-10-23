# 統合ブランチでの導入

稼働中のmainは保ち、GitHub上で `integration/v2-dashboard` などのブランチを作成してください。
この成果物はリポジトリへの直接反映・マージ・公開を行っていません。

1. 統合ブランチに、このフォルダの**中身**を配置します。
   `models/v1/`〜`models/v5/`、`dashboard/`、`tests/`、`.github/` 等が直下になります。
2. 既存の `predictions/JP/`・`US/`、`output/evaluations/`、
   `predictions/v2/`、`output/v2/evaluations/`、`data/universes/` は、そのまま残します。
   既存CSV・JSON内の相対パスを壊さないため、移動・改名しません。
3. 旧 `.github/workflows/market-forecast.yml` は付属の案内用ワークフローへ置き換えます。
   統合ブランチには予約実行がないため、mainの旧V1はこの時点では影響を受けません。
   別名の旧予測ワークフローがある場合、マージ時の二重実行を避けるため停止設定も用意します。
4. Actionsで `V1 daily forecast` を選び、統合ブランチ・市場JPを手動実行。
   元のV1推定コードを使って `predictions/v1/JP/` に新規出力されることを確認します。
   旧履歴に同じ版・観測日の予測が既にある場合、重複作成を避けてスキップします。
5. `V2 daily forecast` を統合ブランチで手動実行。
   初回は `M1`、FREDキー登録後は `ALL` を選択します。
6. `predictions/v2/`、`evaluations/`、`records/`、`dashboard/data.json`、
   Actions artifactの `docs/index.html` を確認します。
   初日の未実現値は空欄が正常です。5日先・20日先は満期まで照合されません。
7. Streamlitアプリのエントリを `dashboard/app.py`、対象を統合ブランチとして設定して確認。
   本番用とは別アプリで確認するか、ローカルで `python -m streamlit run dashboard/app.py` を実行します。
8. 外部取得・保存・グラフ反映が確認できたらmainへマージ。
   Streamlitをmainへ接続し、V1/V2の両予約ワークフローが有効なことを確認します。

## ローカル検証

プロジェクト直下で実行します。

```bash
pip install -r requirements.txt
python -m pytest -q
python models/v1/run.py --help
python models/v2/main.py --help
python -m dashboard.normalize
python -m dashboard.build
python -m streamlit run dashboard/app.py
```

元のroot `main.py`・`config.py` 等が残っていても、新しいワークフローは呼びません。
稼働確認後に必要に応じて別途整理してください。過去履歴を削除する手順は含みません。

## 旧V2履歴の移行上の注意

旧 `predictions/v2/` はそのまま継続して読みます。
旧 `output/v2/evaluations/` も共通ビュー・正規化で読みます。
新しいV2の実績保存先は `evaluations/v2/` です。
旧評価を新保存先の既評価キーとして扱う互換処理を含み、同じ予測を再評価しません。

V1の実績CSVは元仕様に従います。将来の調整価格の訂正などによる違いを消すために、
元のV1評価をV2の方式で勝手に書き換えることはしません。
その制約を理解して、同じ期間・銘柄・リターン定義で比較してください。
