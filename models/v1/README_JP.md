> これは動作版V1の参考資料です。統合版の入口・保存先・自動実行はプロジェクト直下のREADME_JP.mdを参照してください。

# Stock Forecast V1.4（日米株価予測・GitHub Actions版）

このバージョンでは、日本株の5銘柄サンプルを **TOPIX500** に変更しました。米国株は従来どおりS&P500構成銘柄が対象です。

- **セットアップ、日米の自動実行時刻、公式構成銘柄の取得方法、予測ログの保護、テスト結果・制限事項**は [README_TOPIX500.md](README_TOPIX500.md) を参照してください。
- `data/jp_tickers.csv` および `data/jp_tickers_example.csv` は旧バージョンのサンプルで、**新しい日本株予測には使われません**。
- 既存の `predictions/JP/`, `predictions/US/`, `output/evaluations/` は削除・上書きしないでください。

```bash
pip install -r requirements.txt
python topix500_universe.py  # 公式TOPIX500構成銘柄500件を検証・固定
python main.py --market JP   # 500銘柄の日本株予測
python main.py --market US   # 米国株予測
```
