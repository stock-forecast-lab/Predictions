# 共通保存形式 / schema_version 1

予測は1行＝市場×観測日×銘柄×予測期間×版×情報集合×モデル。
作成済み予測は変更せず、満期後の実績は別ファイルに追加します。

| 項目 | 例・意味 |
|---|---|
| schema_version | `1` |
| forecast_id | 保存元・銘柄・期間・情報集合・モデルから作るSHA-256識別子 |
| source_forecast_file | 元の予測ファイルへの相対パス |
| market | `JP` / `US` |
| system_version | `V1` / `V2` / `V3` / 将来の `V4`・`V5` |
| model_version | `v1.4-topix500-membership` / `v2.0.0` などの実装版 |
| model | `OLS`, `Ridge`, `Lasso`, `RF`, `XGB`, `Ensemble` など |
| information_set | `M1`, `M2`, `M3`, `M3_CONTROL`, `M3_FINANCIAL` など |
| horizon_sessions | 取引所営業日で数えた期間。現行は `1` / `5` / `20` |
| ticker | 文字列。日本株の `.T`、米国株の記号を維持 |
| forecast_created_at_utc | 予測を作成したUTC時刻 |
| data_last_observation_date | 学習・予測に使う株価の最終観測営業日 |
| forecast_for_session_date | 累積リターンの終点となる営業日 |
| predicted_return | 小数。`0.01` は1% |
| return_basis | 現行株式は `adjusted_close_ratio`（配当・分割調整後終値の比率） |
| settings_id | 学習期間・モデル選択等の設定識別子。V1は `legacy` |

実績ファイルには `forecast_id`、`actual_return`、`realized_session_date`、
`evaluated_at_utc` を追加します。予測と実績を同じCSVへ上書き保存しません。

## 現行コードとの接続

- V1の推定コードは変更せず、元のwide形式（各モデルが列）のまま保存。
- V2・V3はlong形式（`prediction`, `horizon`）で保存。
- `dashboard/normalize.py` が両形式を上表の共通形式に変換し、
  `records/predictions/` と `records/evaluations/` に追記。
- V1の評価CSVにEnsembleの実績しかなくても、同じ元予測ファイル・銘柄・期間の実績を
  各モデルの予測に結び付けます。実績値そのものをモデルごとに作り直しません。
- 初回の正規化後は元ファイルと正規化ファイルのSHA-256を検証し、変更を検出したら停止。
- V3はこの共通形式で保存します。将来のV4・V5も各版の `predictions/v4/JP/` 等に出力すれば、同じアダプタで表示可能。
  評価は既存と同じlong形式で `evaluations/v3/` 等に追加してください。
  `horizon_sessions`/`predicted_return` とV2の別名 `horizon`/`prediction` の両方を読めます。

既存の `predictions/JP/`、`predictions/US/`、`output/evaluations/` も読みます。
既存ファイルの移動・改名・上書きは不要です。
重複実行があれば、同じ版・設定・情報集合・期間・観測日・対象日・銘柄・モデルの最初の予測を表示します。
変更した学習仕様は、実装版または設定識別子を必ず更新してください。

## 比較ルール

同じ市場・観測日・対象営業日・期間に履歴が存在する全版／設定／モデルの銘柄積集合を
「共通銘柄」とします。日付ごとに構成は変わります。
画面の共通銘柄モードでは、さらに選択した全系列の観測日・対象日が重なる起点だけを表示します。
共通銘柄がなければ空欄とし、各版の全銘柄を混ぜた平均を共通サンプルとは呼びません。
実績カバレッジ95%以上で精度を表示し、誤差は実績が揃う同じ銘柄の予測・実績で計算します。
未来の対象日・引け前の当日実績は精度に含めません。
20営業日先の予測は日次リターンと重ねず、観測日から対象日までの累積指数リターンと比較します。

V1は元の仕様どおり1営業日先のみです。V2の5・20日先にV1の実績を流用しません。
版の差には特徴量だけでなく学習期間・ラベル処理等の差が含まれます。
予測力の差と、追加情報の因果効果は区別してください。

## V3の追加監査情報

V3の元予測CSVには `fin_source_ids`、`train_rows`、`train_sessions`、`train_start`、`train_end`、
`train_last_label_end`、`training_sample_sha256` を保存します。
同じ予測期間のCONTROL／FINANCIALは同じ学習サンプルハッシュを持ちます。
JSONには設定ハッシュ、金融・マクロ出典、対象集合、財務カバレッジ、
予測時点特徴量CSVの相対パスとSHA-256を記録します。元の監査情報は共通集計後も保持されます。
