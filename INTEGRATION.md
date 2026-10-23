# V1・V2・V3統合 / 20261024-v1-multihorizon-r2

今回のV1拡張・mainへの更新手順は [UPDATE_V1_JP.md](UPDATE_V1_JP.md) を参照してください。

今回の更新は**全ファイル入りの完全版**です。以前のパッチを適用する必要はありません。
**アップロード・Secrets設定・V3の実行手順は [INSTALL_V3.md](INSTALL_V3.md) を参照してください。**

## 配置と履歴の扱い

ZIPを展開した中身を既存リポジトリの `main` 直下へ上書きします。履歴は削除せず残します。
`models/`、`data/`、`predictions/`、`evaluations/`、`dashboard/`、`tests/`、`.github/` が同じ階層です。
V4・V5の追加先は `.gitkeep` で保持します。V3には今回実装が入りました。

既存の `predictions/JP/`・`US/`、`output/evaluations/`、`predictions/v2/`、
`output/v2/evaluations/`、`data/universes/` と新しい履歴は、削除・移動・改名しません。
元のCSVやJSONにある相対パスを維持します。旧形式も共通の読み込み処理が参照します。
V1の元の評価結果を、別の価格・ラベル処理で書き換えることはしません。

## 版の分離

- V1：元の推定器は `models/v1/main.py` に維持。`run.py` → `multihorizon.py` から1・5・20日先を直接予測します。
- V2：従来の情報集合M1/M2/M3、light/standard、FRED修正、保存競合対策を含みます。
- V3：V2のエンジンを専用ブリッジ経由で読み、同一学習サンプルに企業情報を追加して比較します。
  V2の推定コードは書き換えず、V3固有の取得・時点整合・保存を `models/v3/` に分離します。
- 共通部分：履歴読み込み・正規化・実績集計・Web画面。

V3 CONTROLはV3内の比較用推定です。V2本体の保存済み予測と同じ結果になる保証はありません。
新V1は1・5・20日先で比較できます。旧V1.4は1日先のみです。共通銘柄・日付・リターン定義を確認してください。

## GitHub Actions

V1/V2/V3を別のワークフローで動かします。同じ版・ブランチの多重実行をconcurrencyで制限します。
版をまたいだ長時間の取得待ちを避けるため、全版を単一のconcurrencyグループにはしていません。
同時保存は `scripts/publish_results.py` が最新HEADに新規履歴を追加して処理します。
生成HTMLをrebaseせずに再生成し、push時に先行更新があれば再試行します。同名で内容が異なる不変履歴は停止します。
Artifactsを先にアップロードするため、コミットが失敗しても出力を取り出せます。

旧 `.github/workflows/market-forecast.yml` は同梱の案内用定義です。
mainへ統合する際は、別名の古い予約予測が残っていないかも確認してください。
GitHubのWorkflow permissionsはリポジトリへの書込みが許可された設定にします。
組織のブランチ保護等によりpushできない場合はArtifactsから出力を確認してください。

## ローカル検証

Python 3.12、プロジェクト直下で実行します。

```bash
pip install -r requirements.txt
python scripts/verify_release.py
python scripts/run_tests.py
python -m models.v3.demo --market BOTH
python -m models.v3.main --market JP --base-set M1 --sync-only
python -m models.v3.main --market JP --base-set M3 --profile light
python -m dashboard.normalize
python -m dashboard.build
python -m streamlit run dashboard/app.py
```

V2は `python models/v2/main.py ...`、V3は **`python -m models.v3.main ...`** です。
旧V2のimport構成と混ざらないよう、共通・V2・V3テストは別プロセスで実行します。
通常の `pytest` は共通テストのみです。全検証には `scripts/run_tests.py` を使います。

サイト公開やリポジトリへの反映は、このZIPを受け取っただけでは行われません。
Streamlitは `dashboard/app.py` をエントリに指定し、APIキーを渡さず保存済み結果を表示します。
