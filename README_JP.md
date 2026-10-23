# Stock Forecast V1.1（日米・GitHub Actions自動実行版）

公開市場データを使い、日米市場の引け後に翌取引セッションの個別株リターンを予測する研究用プロトタイプです。

## GitHub Actions
`.github/workflows/market-forecast.yml` は平日に自動実行します。

- 日本: 07:30 UTC = 16:30 JST（東証引け後）
- 米国: 22:30 UTC（米国現物市場引け後。DST/標準時のどちらでも引け後）
- `workflow_dispatch` から JP / US / BOTH の手動実行も可能

リポジトリの **Settings → Actions → General → Workflow permissions** で `Read and write permissions` を許可してください。

## 重要: 予測ログは不変
真正予測は `predictions/JP/` と `predictions/US/` に保存します。ファイル名には

- データ最終観測日
- 予測対象取引日
- `MODEL_VERSION`
- UTC生成時刻
- GitHub Run ID / attempt
- ランダムsuffix

を含め、CSVは `mode='x'`（create-only）で作成します。既存の予測CSVを再利用・上書きしません。同時実行も `concurrency` で直列化しています。

各予測CSVには `model_version`, `forecast_created_at_utc`, `forecast_for_session_date`, `data_last_observation_date`, `close_at_forecast` を保存し、同名のJSON manifestも残します。

## 翌営業日の照合
各実行では新しい予測を作る前に、過去の未評価予測を読み込みます。各銘柄について「予測時点の最終観測日より後に得られた最初の取引日」の終値を実現値として、

`actual_return = realized_close / close_at_forecast - 1`

を計算します。結果は `output/evaluations/<market>/` に**新規ファイルとしてのみ**保存し、元の予測CSVは変更しません。

保存項目には予測値、実現値、誤差、方向一致、予測対象日、実現取引日、model version、元forecast fileを含みます。

## 日本株ユニバース
`data/jp_tickers.csv` を作り、Yahoo Finance形式のtickerを入れてください。

```csv
ticker
7203.T
6758.T
9984.T
```

サンプルは `data/jp_tickers_example.csv` にあります。

## ローカル実行
```bash
pip install -r requirements.txt
python main.py --market JP
python main.py --market US
```

## 研究上の注意
- US universe は現在のS&P 500構成を取得するため、過去バックテストには survivorship bias が入り得ます。
- Yahoo/yfinance は研究プロトタイプ用です。正式研究では point-in-time universe、corporate actions、データライセンスを別途検証してください。
- V1.1では `target` の翌日shiftをticker内で行うよう修正し、銘柄境界で別銘柄のリターンが混入しないようにしています。
