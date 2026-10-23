from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
VERSION = 'v3.0.0'
PARSER_VERSION = '1'
FINANCIAL_FEATURES = [
    'fin_log_assets', 'fin_margin', 'fin_roa_annualized', 'fin_roe_annualized',
    'fin_equity_ratio', 'fin_liabilities_ratio', 'fin_cashflow_assets_annualized',
    'fin_revenue_growth', 'fin_log_market_cap_estimate', 'fin_earnings_yield',
    'fin_book_to_market', 'fin_log_per', 'fin_log_pbr', 'fin_log_abs_eps',
    'fin_eps_negative', 'fin_age_days',
]
# Priority order is deliberate. Scope and currency must also agree.
SEC_TAGS = {
 'revenue': ['RevenueFromContractWithCustomerExcludingAssessedTax','Revenues','SalesRevenueNet','Revenue'],
 'income': ['NetIncomeLossAvailableToCommonStockholdersBasic','NetIncomeLoss','ProfitLoss'],
 'assets': ['Assets'],
 'equity': ['StockholdersEquity','EquityAttributableToOwnersOfParent','Equity'],
 'liabilities': ['Liabilities'],
 'cfo': ['NetCashProvidedByUsedInOperatingActivities','CashFlowsFromUsedInOperatingActivities'],
 'eps': ['EarningsPerShareBasic','BasicEarningsLossPerShare'],
 'shares': ['CommonStockSharesOutstanding','EntityCommonStockSharesOutstanding'],
}
EDINET_TAGS = {
 'revenue': ['NetSalesSummaryOfBusinessResults','RevenueIFRSSummaryOfBusinessResults','OperatingRevenue1SummaryOfBusinessResults','NetSales','Revenue','RevenueIFRS','OperatingRevenue1'],
 'income': ['ProfitLossAttributableToOwnersOfParentSummaryOfBusinessResults','ProfitLossAttributableToOwnersOfParentIFRSSummaryOfBusinessResults','NetIncomeLossSummaryOfBusinessResults','ProfitLossAttributableToOwnersOfParent','ProfitLossAttributableToOwnersOfParentIFRS','ProfitLoss'],
 'assets': ['TotalAssetsSummaryOfBusinessResults','TotalAssetsIFRSSummaryOfBusinessResults','Assets','AssetsIFRS'],
 'equity': ['EquityAttributableToOwnersOfParentIFRSSummaryOfBusinessResults','EquityAttributableToOwnersOfParent','NetAssetsSummaryOfBusinessResults','NetAssets','EquityIFRS'],
 'liabilities': ['Liabilities','LiabilitiesIFRS'],
 'cfo': ['NetCashProvidedByUsedInOperatingActivitiesSummaryOfBusinessResults','CashFlowsFromUsedInOperatingActivitiesIFRSSummaryOfBusinessResults','NetCashProvidedByUsedInOperatingActivities','CashFlowsFromUsedInOperatingActivitiesIFRS'],
 'eps': ['BasicEarningsLossPerShareSummaryOfBusinessResults','BasicEarningsLossPerShareIFRSSummaryOfBusinessResults','BasicEarningsLossPerShare','BasicEarningsLossPerShareIFRS'],
 'issued_shares': ['TotalNumberOfIssuedSharesSummaryOfBusinessResults','NumberOfIssuedSharesAsOfFiscalYearEndIssuedSharesTotalNumberOfSharesEtc'],
 'treasury_shares': ['NumberOfTreasurySharesHeldByCompany','NumberOfTreasurySharesHeldByCompanyTreasurySharesEtc'],
}
DURATION_METRICS = {'revenue','income','cfo','eps'}
