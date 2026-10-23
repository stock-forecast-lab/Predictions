# V3完全版 20261024-v3-r1：最初に読む手順

V1・V2・V3・共通ダッシュボード・自動実行設定を含む完全版です。以前の差分ZIPを追加適用する必要はありません。
今回はGitHubへ直接反映していません。次の手順で統合ブランチに配置してください。

## 1. 全ファイルを更新

1. ZIPを新しい空のフォルダにすべて展開します。
2. GitHubの `stock-forecast-lab/Predictions` で **integration/v2-dashboard** を開きます。
3. 展開した**中身**をリポジトリ直下にアップロードします。ZIPそのものや親フォルダはアップロードしません。
4. 隠しフォルダの `.github` と `.streamlit`、隠しファイルの `.gitignore` も含めて更新します。
   `.github/workflows/` には `v1-forecast.yml`・`v2-forecast.yml`・`v3-forecast.yml` が入ります。
5. **既存の予測・評価・データ履歴を削除せず**にコミットします。本ZIPには利用者の実際の過去予測CSVは入っていません。
6. GitHub上で `RELEASE.txt` の先頭が **stock-forecast complete 20261024-v3-r1** になっていることを確認します。

各Actionsは推定前にファイルの一致を検証します。`Re-upload:` が出た場合は、表示されたファイルを含めて完全版を再アップロードしてください。
既存のV1/V2実行中に更新すると、その実行は開始時の古いコードを使い続けます。更新後は **Run workflow** で新しく開始してください。**Re-run jobsは使いません。**

## 2. Secretsを設定

GitHubリポジトリの **Settings → Secrets and variables → Actions → New repository secret** に登録します。

| Secret名 | 設定する値 | 用途 |
|---|---|---|
| `EDINET_API_KEY` | EDINET API v2で発行したキー | 日本の財務書類取得 |
| `SEC_USER_AGENT` | アプリ名＋自分の連絡先。例：`StockForecastResearch your-address@example.com` | 米国SECへのアクセス識別。メール部分を自分のものに変更 |
| `FRED_API_KEY` | すでに登録済みならそのまま | V2/V3のM2・M3 |
| `JQUANTS_API_KEY` | 必要な場合のみ | TOPIX/TOPIX500の指数実績。V3財務取得とは別 |

EDINET：[公式API案内](https://disclosure2.edinet-fsa.go.jp/guide/static/disclosure/WZEK0110.html)。APIキー発行手続きは同サイトの案内に従ってください。
SECではAPIキーを発行せず、`SEC_USER_AGENT` に実際の連絡先を指定します。
**キーはソースファイルやチャットに書かず、Secretsだけに登録してください。**

## 3. ActionsにV3が出ない場合だけ、mainに登録

すでに **V3 daily forecast** が見える場合はこの節を飛ばします。

1. mainブランチで **Add file → Create new file**。
2. ファイル名を **`.github/workflows/v3-forecast.yml`** と入力します。
3. ZIP内の **`setup/main-registration-v3.yml` の中身**を貼り付けてmainにコミットします。
   これは手動実行ボタンを登録するだけの定義で、予約実行・予測処理はありません。
4. Actions → **V3 daily forecast → Run workflow** を開き、**Branchをintegration/v2-dashboardに変更**します。

mainの既存V1/V2ファイルは、この登録のために変更する必要はありません。
登録用定義を統合ブランチの本物の `v3-forecast.yml` に上書きしないでください。

## 4. 初回は取得だけで確認

**Actions → V3 daily forecast → Run workflow**：

| 入力 | 最初の値 |
|---|---|
| Branch | `integration/v2-dashboard` |
| market | `JP` |
| mode | `sync` |
| base_set | `M1` |
| profile | `light` |
| max_scan_days | `730` |

日本の取得・保存が確認できたら、米国は `market=US`、`mode=sync` で確認します。
`sync` は財務履歴を取得するだけなのでFREDキーを使いません。
日本は直近から1日ずつEDINETを確認し、完了した日と取得済み書類を保存します。
次回は続きから進みます。直近7日分は再確認します。
初回は多数の書類を取るため、軽量モデルを選んでもデータ収集自体には時間がかかります。

## 5. 予測を実行

同じ画面で `mode=forecast` に変更します。FREDキー登録済みなら `base_set=M3`、最初は `profile=light` を使ってください。
FREDを切り離して試す場合は `base_set=M1` にします。市場は最初はJPかUSの片方ずつにします。

成功時は以下が保存されます。

- `predictions/v3/JP/` または `US/`：企業情報なしの `M3_CONTROL` と企業情報ありの `M3_FINANCIAL`。
- `data/fundamentals/v3/`：書類ごとの財務値、取得進捗、予測時点の特徴量監査記録。
- `evaluations/v3/`：予測対象日の引け後に別ファイルで追加する実績。
- 共通の `records/`・`dashboard/data.json`・`docs/index.html`：V1/V2/V3比較用。

**`V3 BOOTSTRAP` は履歴不足を表します。予測はまだ作っていません。**
財務情報が確認できる銘柄が対象集合の80%以上、かつ1・5・20日先それぞれで満期済みの学習日が252営業日以上になるまで取得を続けます。
取得対象をすべて確認しても条件を満たさない場合はエラーで止め、欠測のまま推定した結果を成功扱いにしません。
API側の履歴提供範囲、XBRL標準タグの有無などで取得できない値があります。

Actionsの **Summary** で進捗・カバレッジ・保存先を確認できます。Artifactsにも保存します。
画面ではV3と、`M3_CONTROL`／`M3_FINANCIAL` を選択して比較できます。
M1で実行した場合のラベルは `M1_CONTROL`／`M1_FINANCIAL` です。

## 6. 運用へ反映

統合ブランチで外部API・予測・保存・画面更新を確認してからmainにマージしてください。
mainに本物のワークフローが入ると予約実行の対象になります。
V1/V2は従来の時刻、V3は08:30 UTC（日本）と23:30 UTC（米国）です。
mainが登録用の定義のままではV3の予約予測は動きません。

V1/V2/V3は版ごとに実行待ちを分け、GitHubへの保存時は最新ブランチに新規履歴を追加して再試行します。
同じ版・ブランチの実行を大量に連打しないでください。

## 検証範囲

ローカルの合成データ・模擬API・実Git競合テストは実施済みです。
**EDINETキー・SECの実際の連絡先を使った全銘柄取得と、V3のGitHub Actions本番実行は未検証です。**
ダッシュボードのデモは合成データで、V3の精度改善を示すものではありません。
仕様と指標の定義は `models/v3/README_JP.md`、検証記録は `VALIDATION.md` を参照してください。
