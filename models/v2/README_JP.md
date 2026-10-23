# 株価予測システム V2.0

V1.4を保存し、公開市場データに為替・金利・マクロ指標を追加した実装です。
TOPIX 500 / S&P 500について、1・5・20営業日先までの調整後終値の累積リターンを予測します。
V2は将来株価の保証や売買注文の実行を行うものではありません。

## 統合版での利用

配置・自動運用・Streamlitの手順はプロジェクト直下の `README_JP.md` を参照してください。
この資料はV2の推定・データ・評価の説明です。

## 比較する情報集合

| 集合 | 説明変数 |
|---|---|
| M1 | 個別株の1/5/20/60日リターン、ボラティリティ、出来高、値幅、移動平均乖離、市場平均リターン、相対リターン |
| M2 | M1 + USD/JPY、米2年・10年金利、長短金利差、米信用スプレッド、VIXと各変化量 |
| M3 (US) | M2 + 米CPI前年比、鉱工業生産前年比、失業率、月次実効FF金利 |
| M3 (JP) | M2 + **米CPI前年比（国際的な物価指標）**、日本の鉱工業生産前年比、失業率、月次コール金利、月次10年国債利回り |

日本のCPIは旧FRED系列が2021年で止まっているため、自動取得版には含めていません。
米CPIを日本CPIとして扱っていません。日本の生産指数は更新が続く前年比系列を採用しています。
日本の短期金利は政策金利の誘導目標そのものではなく月次コール金利です。
日米で使う系列と変換方法は `config.py` に明記しています。

同じ実行のM1/M2/M3は、利用できる特徴量が揃う**同じ銘柄・同じ学習行**で比較します。
したがってM1単独実行とM1/M2/M3同時実行ではサンプルが異なる場合があります。
V2のM1はV1系の比較基準です。重複していたモメンタム変数の整理、学習期間、ラベル処理の改善があるため、
過去のV1.4予測と数値まで一致するものではありません。
V1.4のソースは `models/v1/` に保存しています。過去の予測履歴は含めず、そのまま既存の場所に残します。

モデルは Historical Mean / OLS / Ridge / Lasso / Elastic Net / Random Forest / XGBoost。
EnsembleはHistorical Meanを除いた推定モデルの単純平均です。
モデルは銘柄ごとではなく、市場ごと・期間ごと・情報集合ごとのプール推定です。
学習は直近756営業日（約3年）、最低252営業日。係数とモデル設定は固定で、
テスト期間でのチューニングや自動的な「最良モデル」選択はしていません。

## ローカルで動かす

Python 3.12を推奨します。

```bash
pip install -r requirements.txt
export FRED_API_KEY='自分のキー'
python models/v2/main.py --market JP
python models/v2/main.py --market US
```

小さな構成での初回動作確認：

```bash
python models/v2/main.py --market JP --sets M1 M2 M3 --horizons 1 --models HistoricalMean Ridge
```

キーなし・通信なしの合成データによる接続確認：

```bash
python -m pytest -q
python models/v2/demo.py
```

`demo.py` は日米・3情報集合・3期間・全モデルを接続して確認します。
デモだけ木の本数を10本に減らします。出力は `output/demo/` で、すべて合成データと明記します。
この結果は実際の株価予測成績ではありません。

## 出力と実績照合

- `predictions/v2/JP/`、`predictions/v2/US/`：予測CSVと対応JSON。
- `evaluations/v2/JP/`、`evaluations/v2/US/`：満期を迎えた予測の実績照合。
- `data/universes/`：実行時の銘柄集合スナップショット。
- `data/context_cache/`：取得したALFRED観測・改定履歴。Git管理対象外。
- `output/v2/backtests/`：別途指定して実行する疑似OOS検証。

予測CSVは1行＝銘柄×期間×情報集合×モデルのlong形式です。
予測作成時刻、モデル版、最終観測日、予測対象営業日、学習行数、学習ラベルの最終満期日を記録します。
JSONには特徴量、設定、銘柄集合、経済系列のハッシュと最終利用時点も記録します。
同一設定・観測日・版での重複を抑止し、作成済みの予測・評価CSVは上書きしません。
対応JSONのない不完全な出力は完成済み予測として扱いません。

実績は、後日の実行で**記録した対象営業日の価格が得られたときだけ**照合します。
5日先は5営業日後、20日先は20営業日後に評価します。
対象日の価格が欠けた銘柄は保留し、別の日の価格で代用しません。
分割・配当後に調整後終値が遡及変更されるため、照合時点の同じ価格系列から始点と終点の比率を計算します。
予測時に保存した調整後終値は監査用として残します。実績は最初に照合した値を固定保存します。

日米いずれかの実行が失敗しても、成功した市場の完成済みログはコミット・Actions artifactで保存する構成です。
外部接続・ブランチ保護・Actions障害などで保存自体が失敗する可能性はログで確認してください。

## バックテスト

最初は範囲・期間・モデル数を絞ってください。全銘柄×3集合×3期間×全モデルの毎回再学習は重い処理です。

```bash
python models/v2/main.py --market JP --backtest --backtest-start 2025-01-01 --max-origins 12 --horizons 20 --models HistoricalMean Ridge
```

全期間の比較：

```bash
python models/v2/main.py --market JP --backtest --backtest-start 2024-01-01
```

各hについてh営業日おきの非重複予測を作ります。学習には予測時点より前に満期が判明したラベルだけを使います。
出力はOOS R²（各時点の学習標本平均が基準）、RMSE、MAE、方向一致率、日次横断面Rank IC、
ロングショート収益、Sharpe、最大ドローダウン、回転率、取引費用、年率Alpha/Betaです。
Rank ICは予測起点ごとの値を平均します。

ロングショートは上位10%を資産の50%ロング、下位10%を50%ショート。
費用は取引額1単位あたり10bpが初期値で、建玉・組替え・最終清算を含みます。
全予測が同じHistorical Meanは順位を作らず現金ポジションとします。
Alphaは同じ評価銘柄の等ウェイト市場リターンに対する回帰切片で、公式指数へのAlphaではありません。
12期間未満ではAlphaは算出しません。Sharpeは無リスク金利を引かない年率化指標です。
空欄はサンプル不足・定数予測などにより定義できない値です。

### 評価の解釈

この検証は現在の構成銘柄と現在の調整後価格を使う疑似OOSです。
構成銘柄の生存者バイアス、価格訂正、上場廃止銘柄の欠落は除去していません。
当時の全銘柄構成を復元した完全なpoint-in-timeバックテストではありません。
また、終値を見て予測し同じ終値から収益を測るため、ロングショート指標は予測力の診断です。
その終値で約定できたことを意味せず、翌営業日の執行や借株料・市場インパクトは含みません。

## 情報時点と欠損の扱い

FREDの最新版CSVに固定ラグを付けるのではなく、ALFREDのreal-time期間を持つ観測履歴を取得し、
各株式市場の終値時点で利用できる改定版だけを再生します。
公表時刻を持たない系列については、FRED/ALFREDのビンテージ日から2暦日後の00:00 UTCまで利用しません。
当局の最速公表より遅い情報集合になりますが、公表日前にマクロ値を先取りしません。
CPI・米生産指数の前年比は、その時点で分かっていた前年値で計算します。
過去月の改定で「最新観測月」が逆戻りすることも防ぎます。

日次外生系列の観測が14日、月次系列が180日を超えて古い場合は停止します。
開始直後の欠損を未来から埋めることはせず、必要な特徴量が揃った行で比較します。
最新の完了済み取引日を取引所カレンダーで決め、形成途中の日足は除外します。
最新日の銘柄・特徴量カバレッジが95%未満なら、新規予測を保存しません。
TOPIX 500はV1.4の2026年10月公式分類スナップショットを引き継ぎます。
今後の分類変更は `topix500_universe.py` の公式資料と検証数の更新が必要です。
2027年10月以後は更新を促して停止しますが、途中の臨時変更も運用者が確認してください。

## データ出典

- [FRED/ALFRED observations API](https://fred.stlouisfed.org/docs/api/fred/series_observations.html)
- [Real-time periods](https://fred.stlouisfed.org/docs/api/fred/realtime_period.html)
- M2: [DEXJPUS](https://fred.stlouisfed.org/series/DEXJPUS)、[DGS2](https://fred.stlouisfed.org/series/DGS2)、[DGS10](https://fred.stlouisfed.org/series/DGS10)、[BAA10Y](https://fred.stlouisfed.org/series/BAA10Y)、[VIXCLS](https://fred.stlouisfed.org/series/VIXCLS)
- US: [CPIAUCSL](https://fred.stlouisfed.org/series/CPIAUCSL)、[INDPRO](https://fred.stlouisfed.org/series/INDPRO)、[UNRATE](https://fred.stlouisfed.org/series/UNRATE)、[FEDFUNDS](https://fred.stlouisfed.org/series/FEDFUNDS)
- JP: OECD Main Economic Indicators via FRED、[生産前年比](https://fred.stlouisfed.org/series/JPNPRINTO01GYSAM)、[失業率](https://fred.stlouisfed.org/series/LRHUTTTTJPM156S)、[コール金利](https://fred.stlouisfed.org/series/IRSTCI01JPM156N)、[10年利回り](https://fred.stlouisfed.org/series/IRLTLT01JPM156N)
- 株価: Yahoo Finance via yfinance。S&P 500構成: [datasets/s-and-p-500-companies](https://github.com/datasets/s-and-p-500-companies)。TOPIX 500構成: V1.4のJPX公式資料。

再配布・公開時は各データ提供元の利用条件と必要な出典表記を確認してください。
