# Stock Forecast V1.3 — 日米500銘柄版

## 変更点

- JP: 日本株の従来の **5銘柄のサンプル**を廃止し、JPX公式 TOPIX500（Core30 + Large70 + Mid400）の500銘柄を使用します。
- US: GitHub公開S&P500構成銘柄CSVを使用します（従来のWikipedia 403回避版を維持）。
- モデル: OLS, Ridge, Lasso, Random Forest, XGBoost と等ウェイトEnsemble。統計モデルの設定は従来どおり。
- モデルバージョン: `v1.3-topix500`。
- 予測CSV、付随JSON、評価CSVは新規ファイルとして保存し、**既存の予測ファイルを変更しません**。
- 予測日時・予測対象セッション・最終観測日・モデルバージョンのほか、**公式銘柄集合のスナップショット名・SHA256**を予測に記録します。
- 同じモデルバージョン・同じ最終観測日については二重の予測を作りません。祝日も同様です。
- 事後評価は **指定した予測対象セッション** の実現終値と比較します。対象日の株価がない銘柄は未評価のまま残します。
- 日米とも90分→180分へワークフロー上限を変更。5種類のモデル設定は変えていません。

## JPX公式構成銘柄の扱い

正式な指数構成区分が載っている2026-10-07公表JPX PDFを、`topix500_universe.py` が取得して解析します。

https://www.jpx.co.jp/news/6030/t13vrt00000262wd-att/mei2_12_size.pdf

表の旧区分は **2026年10月7日時点**、新区分は **2026年10月30日から適用**。`2026-10-30` より前は旧区分、以降は新区分を使います。両方の区分で Core30=30、Large70=70、Mid400=400、合計500を検証します。

検証された銘柄集合は次の**別々の固定スナップショット**として作成されます。

```
data/universes/JP/topix500_pre_20261030.csv
data/universes/JP/topix500_from_20261030.csv
```

スナップショットは上書きせず、GitHubリポジトリにコミットされます。**旧 `data/jp_tickers.csv` は本番の予測に使用しません。**

### 注意事項

- 構成銘柄は現時点の公式資料に基づきます。日々の新規採用・除外や翌年2027年以降の定期見直しを自動追随するものではありません。**JPXの次回公表時に構成銘柄取得部分を更新する必要があります。**
- 2026年10月7日以降の個別変更が発生している場合も、PDFに含まれない変更については別途更新が必要です。
- `pdfplumber` による公式PDF解析は、実際のGitHub Actionsネットワーク上での初回テストが必要です。HTTP403/表レイアウト変更・検証失敗時は処理を停止し、**5銘柄への黙ったフォールバックはしません**。
- 米国市場との比較は構成銘柄数が近いだけで、指数の組成基準、コーポレートアクション、サバイバーシップ・バイアスは同一にはなりません。
- Yahoo Finance価格データは研究用途のプロトタイプとして使用します。銘柄ごとの取得失敗もあり、JPは当日有効データが475銘柄未満の場合、誤認を避けるため予測を作りません。
- 500銘柄×2015年以降×複数モデルの学習なので実行時間は5銘柄版よりかなり長くなります。

## GitHubの既存リポジトリへの導入

変更・追加するファイルは以下です。`main.py`、`config.py`、`requirements.txt`、`.github/workflows/market-forecast.yml` を差し替え、`topix500_universe.py`、`README_TOPIX500.md`、`.gitignore` を追加してください。

```
.github/workflows/market-forecast.yml  ←既存ファイルを更新
main.py                              ←既存ファイルを更新
topix500_universe.py                 ←新規
config.py                            ←既存ファイルを更新
requirements.txt                     ←既存ファイルを更新
README_TOPIX500.md                   ←新規
.gitignore                           ←新規
```

以前の `.github` はGitHub UIに隠しフォルダとして扱われるため、アップロードできない場合は **GitHubの `.github/workflows/market-forecast.yml` を開いて鉛筆（Edit）で置き換え**してください。

既存の `predictions/JP/` と `predictions/US/` および `output/evaluations/` は触らないでください。

## Actionsでの実行

Actions → Daily JP-US stock forecast → Run workflow → JPを選択。最初の `Prepare official TOPIX 500 membership snapshot` ステップで500銘柄が検証され、次に `Run Japan forecast` で推定が始まります。

生成済みスナップショットは次回以降も再利用します。`data/universes/JP/` はGitHub ActionsがGitにコミットするようになっています。

時刻: JP 平日16:30 JST、US 火曜～土曜7:30 JST（cron遅延・市場休場日は例外）。

## テスト済み・未検証

- コード構文チェック: 済み
- 模擬JPX PDF（500行、30+70+400分類）での抽出テスト: 済み
- GitHub Actions上の公式JPX PDFダウンロードおよび約500銘柄の市場データ取得とモデル推定: **未検証**
