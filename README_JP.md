> 完全版 **20261024-v3-r1**。更新・実行は [INSTALL_V3.md](INSTALL_V3.md) を参照してください。
> V1・V2・V3・共通ダッシュボード・Actionsを含みます。過去のパッチ追加は不要です。
> V3は日本EDINET／米国SECの公表履歴を使用し、企業情報あり・なしを同条件で比較します。

# 株価予測システム / V1・V2・V3並行運用＋共通Streamlitダッシュボード

動作確認済みV1.4を残し、V2を独立した自動実行として追加しました。
Web画面・保存形式はV1〜V5共通です。V3の財務情報追加モデルを実装しました。V4・V5は空の追加先を用意しています。

## できること

- V1・V2・V3をそれぞれ独立して実行。V3は同じ学習行・予測銘柄で企業情報あり／なしを比較。
- 市場・版・設定・情報集合・モデル・1/5/20日先を選んで比較。
- モデル別平均予測、指数との参考差、個別株ランキング、実績照合、散布図。
- RMSE・Rank ICのローリング推移。共通銘柄と各版の全銘柄を切り替え。共通銘柄モードは選択系列の評価期間もそろえます。
- 過去の予測・実績は追記保存。共通形式へ変換して保存元も記録。
- Actionsが集計を更新し、Streamlitがその保存済みスナップショットを表示。
- 閲覧だけならAPIキー不要。HTMLスナップショットも生成。

## 最終構成

```text
stock-forecast/
├── models/
│   ├── v1/
│   ├── v2/
│   ├── v3/  (企業財務情報)
│   ├── v4/  (.gitkeep)
│   └── v5/  (.gitkeep)
├── data/
├── predictions/
├── evaluations/
├── dashboard/
├── tests/
└── .github/workflows/
```

このほか、共通形式の監査用 `records/`、HTML用 `docs/`、設定・手順書を含みます。

## フォルダ

| 場所 | 内容 |
|---|---|
| `models/v1/main.py` | 送付された動作版V1.4の推定コードと同一 |
| `models/v1/run.py` | 新保存先での実行、旧履歴の照合・重複予測の確認 |
| `models/v2/main.py` | 為替・金利・マクロを追加したV2 |
| `models/v3/main.py` | 日本EDINET・米国SECを追加するV3 |
| `dashboard/app.py` | 共通Streamlit画面 |
| `.github/workflows/v1-forecast.yml` | V1の自動実行 |
| `.github/workflows/v2-forecast.yml` | V2の自動実行 |
| `.github/workflows/v3-forecast.yml` | V3の財務取得・予測 |
| `predictions/v1/`, `predictions/v2/`, `predictions/v3/` | 新しい版別予測ログ |
| `evaluations/v1/`, `evaluations/v2/`, `evaluations/v3/` | 版別の実績照合 |
| `records/` | 共通スキーマの追記専用予測・実績 |
| `dashboard/data.json` | Web画面用集計。再生成可能 |
| `docs/index.html` | 外部通信なしで開ける集計画面 |
| `dashboard_demo.html` | 合成データによる画面見本 |

V1のモデル・特徴量・学習期間・予測処理は変更していません。
`config.py` と銘柄集合モジュールの変更はプロジェクト共通データへのパスのみです。
V1の元コードに由来する学習・調整価格の扱い等も維持しています。
V2のM1はV1とは別です。V1の比較系列として置き換えていません。

## GitHubへ配置

1. ZIPを展開し、展開先の**中身**を現在のリポジトリ直下にアップロード。
   `predictions/`、`output/`、`data/universes/` にある既存履歴は削除しないでください。
2. 同名の `.github/workflows/market-forecast.yml` を必ず付属版で更新。
   これは旧スケジュールを停止する案内用エントリです。
   別名の古い予測ワークフローがあれば、そのスケジュールも停止してください。
3. GitHubの **Settings → Actions → General → Workflow permissions** を
   `Read and write permissions` に設定。組織のブランチ保護等によっては追加設定が必要です。
4. **Settings → Secrets and variables → Actions** に `FRED_API_KEY` を登録。
   V1には不要。V2のM2・M3に必要です。
5. Actionsで **V1 daily forecast** と **V2 daily forecast** をそれぞれ手動実行。
   初回は市場を `JP` にすると切り分けやすく、その後 `US` も確認できます。

予約時刻は両方とも07:30 UTC（16:30 JST、日本用）と22:30 UTC（米国の引け後）。
V3は08:30 UTC・23:30 UTCです。版ごとのconcurrencyと、保存時の競合再試行を使います。
V1とV2のジョブは独立しているため、V2がAPIキー不足で失敗してもV1の運用は続けられます。
GitHubの予約実行・キューには遅延があり、稼働はActionsのログで確認してください。

統合は、まず専用ブランチで行うことを推奨します。[導入手順](INTEGRATION.md) を参照してください。

### V2をAPIキーなしで試す

V2の手動実行で `information_sets = M1` を選択。
通常の予約実行はM1/M2/M3を要求するため、M2/M3を継続運用するにはFREDキーが必要です。
FREDキーは [公式ページ](https://fred.stlouisfed.org/docs/api/api_key.html) から取得します。

## Streamlitをオンラインで使う

1. [Streamlit Community Cloud](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/deploy)で
   自分のGitHubリポジトリを接続。
2. 対象ブランチと、エントリ **`dashboard/app.py`** を指定してデプロイ。
3. 公開範囲はアプリ設定で選択。非公開で使う場合はPrivateを確認してください。

このZIPの配置だけでサイトが公開されることはありません。
StreamlitへFREDキーを渡す必要もありません。推定はGitHub Actionsで実行し、画面は読取り専用です。
Actionsが予測・評価・集計をコミットすると、接続したアプリへ変更が反映されます。
画面は60秒ごとに保存済み集計を再確認します。Cloudの反映遅延や休止時は再起動・再読込みが必要な場合があります。
GitHub Pagesの自動公開は含めていません。

公式説明: [GitHub変更の自動反映](https://docs.streamlit.io/deploy/streamlit-community-cloud/manage-your-app)、
[アプリの公開・非公開](https://docs.streamlit.io/deploy/streamlit-community-cloud/share-your-app)。

## 指数データ

| 指数 | 取得方法 |
|---|---|
| 日経平均 | Yahoo Financeの現物価格指数 `^N225` を自動取得 |
| S&P 500 | Yahoo Financeの現物価格指数 `^GSPC` を自動取得 |
| TOPIX | `JQUANTS_API_KEY` を設定した場合、J-Quants V2から取得。または指数CSV |
| TOPIX 500 | 同キー・必要な利用権限がある場合、指数コード `002C` で取得。または指数CSV |

J-Quantsの利用可能範囲は契約・提供期間に依存します。契約・キーがなければ取得済みの指数だけを表示します。
TOPIXをTOPIX 500に置き換えたり、ETFを公式指数として扱ったりはしません。
指数取得失敗時も予測ログは維持し、欠けた指数実績は埋めません。

CSVを使う場合は `data/benchmarks/imports/任意名.csv` に次の列を入れてください。
`market, benchmark, date, close, return_basis, source`。
`benchmark` は `TOPIX` / `TOPIX500` / `NIKKEI225` / `SP500`、日付は `YYYY-MM-DD`。
価格指数なら `return_basis = price_index`。提供元に合わせて出典を記録します。

### 指数ウェイトでの集計

`data/index_weights.csv` に以下の列を用意すると利用できます。
`market,benchmark,snapshot_date,available_at_utc,ticker,weight,expected_count,source,return_basis`。
日付は**予測の観測日そのもの**、公表時点は当日の市場引け以前、ウェイト合計は1、
全構成銘柄が揃い、予測のリターン定義に対応するものが必要です。
不足銘柄を除いて勝手に再加重しません。指数ウェイトの自動取得は含みません。

当初ウェイト固定の近似ポートフォリオであり、途中の構成変更・指数除数の変更までは再現しません。
調整後株価リターンと価格指数の差は、厳密な指数予測誤差ではなく参考差です。

## ローカルで実行

```bash
pip install -r requirements.txt
python models/v1/run.py --market JP
python models/v2/main.py --market JP --sets M1
python -m dashboard.normalize
python -m dashboard.build --refresh-benchmarks
python -m streamlit run dashboard/app.py
```

FREDキーを設定すれば `python models/v2/main.py --market JP` でM1/M2/M3を実行。
V2の設定・系列・バックテストの詳細は `models/v2/README_JP.md` を参照し、
コマンドはプロジェクト直下から `python models/v2/main.py ...` の形で実行してください。
V1は1営業日先、V2は1・5・20営業日先です。

```bash
python scripts/run_tests.py
```

通信なしの画面確認はStreamlitの「合成データで画面例を見る」を使えます。
`dashboard_demo.html` もブラウザで開けます。見本は全て合成データです。

## 確認済み範囲

仕様・データ整合性のテストとStreamlitの操作試験は実施済み。
今回の環境では実際のFRED/J-Quantsキー、GitHubへの書込み権限、Streamlitデプロイ先が未提供です。
そのため全銘柄の実市場実行・予約実行・オンラインデプロイは未確認です。
導入後、まず手動実行で外部データ取得とログ保存を確認してください。

V2は現在の構成銘柄と調整後価格を使うため、過去検証の生存者バイアス等は残ります。
V3と将来のV4・V5も同じ [共通保存形式](SCHEMA.md) を利用できます。

## V3の初回設定

日本は `EDINET_API_KEY`、米国はアプリ名と実連絡先を入れた `SEC_USER_AGENT` をActions Secretsに設定します。
M2/M3には既存の `FRED_API_KEY` も必要です。V3はまず `mode=sync` で履歴取得を確認してください。
V3がActionsに表示されない場合のmainへの登録用ファイルも `setup/` に同梱しています。

初回の財務取得は長くなる場合があります。取得不足は `BOOTSTRAP` と表示して予測を作らず、進捗だけを保存します。
財務特徴量の年率換算・推計PER等の定義と制約は [V3仕様](models/v3/README_JP.md) を参照してください。
実APIによる全銘柄実行と予測力の改善は未検証です。
