# V1を1・5・20営業日先に拡張する / 20261024-v1-multihorizon-r2

このZIPはV1・V2・V3・共通ダッシュボードのコード一式です。
GitHubへの反映・実市場での日米全銘柄実行は、まだ行っていません。

## GitHubで更新する場所

既存の `stock-forecast-lab/Predictions` の **main** を開きます。
ZIPを展開し、最上位フォルダではなく、その中身をリポジトリ直下に上書きアップロードします。
既存リポジトリ自体を削除したり、予測・評価・財務履歴を消したりする必要はありません。

予測CSV・実績CSV・財務データ、生成済み `dashboard/data.json` / `docs/index.html` はZIPに入れていません。
既存の履歴と画面用スナップショットを残したままコードを更新できます。
モデル・データ・設定を含む既存ファイルを一括削除してから入れ直す操作はしないでください。

主な更新箇所：

| ファイル | 役割 |
|---|---|
| `models/v1/multihorizon.py` | 新規。各hの直接予測、実取引所セッションでのラベル、満期後の実績照合、保存 |
| `models/v1/run.py` | 本番入口。新エンジンの呼出、旧履歴の照合、BOTH対応 |
| `.github/workflows/v1-forecast.yml` | 自動実行で1・5・20日先を指定。手動はALL / NEXT_DAYを選択 |
| `dashboard/data.py` | 試運転V1.1を表示・共通銘柄比較から除外 |
| `dashboard/app.py` | V1表示、選択した予測期間ごとの設定一覧、タイトル「株価予測確認グラフ」 |
| `tests/v1/`、`scripts/run_tests.py`、`pytest.ini` | 期間・満期・保存・画面への接続を検証 |
| `.github/workflows/v2-forecast.yml`・`v3-forecast.yml` | テストの分離と、既に決めたstandardの既定値を維持 |
| `release_manifest.json`、`RELEASE.txt` | 新しい一式に対応する検証情報 |

`.github` がアップロード画面に出ない場合、GitHub上で既存の
`.github/workflows/v1-forecast.yml`・`v2-forecast.yml`・`v3-forecast.yml` をそれぞれ開き、
鉛筆から同梱ファイルの全文に置き換えてコミットします。
`market-forecast.yml` は従来の案内用で、残して構いません。

## 実行する

Actions → **V1 daily forecast** → **Run workflow**：

- Branch：**main**
- market：最初はJPまたはUS、両方ならBOTH
- horizons：**ALL**（1・5・20日先。NEXT_DAYなら1日先だけ）

予約実行は、既存のJP/USの時刻で毎回1・5・20日先を出します。
V1のために新しいAPIキーを登録する必要はありません。
V2・V3は既存のFRED / SEC / EDINET設定を使います。EDINET未設定の日本V3は別途接続が必要です。

ローカルでの入口は次のコマンドです。`models/v1/main.py` の直接実行は旧1日先版です。

```bash
python models/v1/run.py --market JP
python models/v1/run.py --market BOTH --horizons 1 5 20
python models/v1/run.py --market US --horizons 1
python models/v1/run.py --market JP --evaluate-only
```

## モデルと過去履歴

元のV1.4の `main.py` は変更せず、そこにある14特徴量とOLS・Ridge・Lasso・RF・XGBoostを再利用します。
新しい処理は `multihorizon.py` に分離しています。Ensembleは従来と同じ単純平均です。
株価取得・特徴量作成は市場ごとに1回、モデルの学習はhごとに実施するため、1日先だけの場合より計算量は増えます。
全銘柄の本番実行時間は未計測です。

目的変数はそれぞれ `P(t+h)/P(t)-1`。h回先の**実取引所営業日**の株価を使います。
対象日の株価が欠ける場合は、その後の別日の株価で代用しません。
学習には起点が予測日より前で、ラベル終点が予測日引けまでに到来している行だけを使います。
当日引け前の株価は対象から除き、最新完了セッションの株価が揃わなければ新予測を停止します。
特徴量の無限大は欠測にし、V1の補完処理へ渡します。

新予測の内部バージョンは **v1.5-multihorizon**。
新しい1日先も、営業日・満期・価格基準のチェックを追加しているため、V1.4と完全に同じ結果とは限りません。
画面表示は「V1」、旧V1.4は「V1 / 旧設定（1日先）」と区別します。
V1.1は削除不要で画面と比較から除外されます。保存済みのCSV・JSON・共通履歴は変更しません。

`predictions/v1/JP/`・`US/` に **hごとに別CSV＋JSON** を追加保存します。
同じ市場・観測日・期間・設定の再実行は保存済み結果を維持します。
途中で長期モデルだけ失敗した場合も、既に完了した期間の予測は残り、再実行で未完了分を続けます。
実績は `evaluations/v1/JP/`・`US/` に保存します。配当・分割調整後株価の同一取得系列から両端を照合します。

V1〜V3の共通画面で各hを比較できます。ただし銘柄・期間・学習窓・特徴量処理まで自動で同一になるわけではありません。
現行構成銘柄に依存するため、過去の指数採用・除外を再現するバックテストではありません。

画面の表示コードはタイトル等を編集できるよう、release_manifestのハッシュ検証対象から外しています。
推定・保存・共通読込などのコードは引き続き検証します。

## 検証

```bash
pip install -r requirements.txt
python scripts/verify_release.py
python scripts/run_tests.py
```

実装上の検証結果は `VALIDATION_V1_MULTIHORIZON.md` を参照してください。
