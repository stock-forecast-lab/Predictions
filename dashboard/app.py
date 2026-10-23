"""Shared V1-V5 Streamlit UI. Reads snapshots; never trains, writes or fetches quotes."""
from pathlib import Path
import sys
import json
import math
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from dashboard.data import build_data,load_forecasts,attach_actuals,load_benchmarks,load_weights

st.set_page_config(page_title='Forecast Observatory | 株価予測',page_icon='📈',layout='wide')
COLORS={'OLS':'#4f77ab','Ridge':'#178b83','Lasso':'#bf9654','RF':'#8b72ad','XGB':'#c66b59','Ensemble':'#173e5c','ElasticNet':'#a6a553','HistoricalMean':'#93a0a7'}
DEFAULT_MODELS=['OLS','Ridge','Lasso','RF','XGB','Ensemble']
BENCH_NAMES={'UNIVERSE_EW':'同じ銘柄の等ウェイト実績','NIKKEI225':'日経平均（価格指数）','TOPIX':'TOPIX（価格指数・参考）','TOPIX500':'TOPIX 500（取込データ）','SP500':'S&P 500（価格指数）'}


def valid(x): return isinstance(x,(int,float)) and math.isfinite(x)
def fmt(x,pct=True): return f'{100*x:.2f}%' if valid(x) and pct else f'{x:.3f}' if valid(x) else '—'
def label(s): return f"{s['version']} · {s['information_set']} · {s['model']} [{s['spec'][:6]}]"


@st.cache_data(ttl=60,show_spinner=False)
def load_data(demo=False):
    if demo:
        saved=ROOT/'dashboard_demo.json'
        if saved.exists():
            data=json.loads(saved.read_text())
            if data.get('synthetic') is not True: raise ValueError('合成データの識別情報がありません')
            return data
        from dashboard.build import demo_data
        return demo_data()
    snapshot=ROOT/'dashboard/data.json'
    if snapshot.exists(): return json.loads(snapshot.read_text())
    return build_data(attach_actuals(load_forecasts(ROOT),ROOT),load_benchmarks(ROOT),load_weights(ROOT))


def get_points(s,sample): return [p[sample] for p in s['points'] if p.get(sample)]


def prediction(p,aggregation,benchmark):
    return p.get('weighted',{}).get(benchmark,{}).get('prediction') if aggregation=='weighted' else p['prediction']


def realized(p,benchmark):
    return p['actual'] if benchmark=='UNIVERSE_EW' else p['reference'].get(benchmark,{}).get('actual')


def chart(title,y_title='%'):
    f=go.Figure()
    f.update_layout(template='plotly_white',title=title,height=370,margin=dict(l=25,r=15,t=50,b=35),
                    font=dict(family='sans-serif',size=11,color='#71818c'),
                    legend=dict(orientation='h',font=dict(size=10)),hovermode='closest',
                    xaxis_title='予測対象営業日',yaxis_title=y_title)
    return f


def add_line(fig,s,dates,values,versions):
    fig.add_trace(go.Scatter(x=dates,y=values,mode='lines',name=label(s),connectgaps=False,
        line=dict(color=COLORS.get(s['model'],'#71818c'),width=2.5 if s['model']=='Ensemble' else 1.5,
                  dash='dash' if versions.index(s['version']+'|'+s['spec'])%2 else 'solid')))


def rolling(points,metric,window):
    eligible=[p for p in points if p['ready']]
    dates=[];values=[]
    for i,p in enumerate(eligible):
        dates.append(p['target'])
        q=eligible[max(0,i-window+1):i+1]
        if len(q)<window or any(not valid(t[metric]) for t in q): values.append(None)
        elif metric=='rmse': values.append(math.sqrt(sum(t['rmse']**2 for t in q)/window)*100)
        else: values.append(sum(t['ic'] for t in q)/window)
    return dates,values


@st.fragment(run_every='60s')
def dashboard():
    st.markdown('<div style="color:#178b83;letter-spacing:3px;font-size:11px">FORECAST OBSERVATORY / V1 → V5</div>',unsafe_allow_html=True)
    st.title('予測を、実績で確かめる。')
    st.caption('日米株式 · モデル別予測 · 市場との比較 · 個別株の実績照合')
    demo=st.toggle('合成データで画面例を見る',value=False,key='demo')
    if demo: st.warning('すべて合成データです。実際のモデル成績・実際の指数ではありません。')
    try: data=load_data(demo)
    except Exception as exc:
        st.error(f'履歴の読み込みを停止しました：{exc}')
        return
    generated=pd.Timestamp(data['generated_at_utc']).tz_convert('Asia/Tokyo').strftime('%Y-%m-%d %H:%M JST')
    st.caption(f'データ更新：{generated} ｜ 画面は60秒ごとに保存済みスナップショットを確認します。')
    if not data['series']:
        st.info('まだ予測履歴がありません。GitHub ActionsでV1・V2を実行すると自動反映されます。画面例は上のスイッチで確認できます。')
        return
    cols=st.columns(5)
    market=cols[0].selectbox('市場',['JP','US'],format_func=lambda x:'日本 / TOPIX 500銘柄' if x=='JP' else '米国 / S&P 500銘柄')
    available=[s for s in data['series'] if s['market']==market]
    horizons=sorted(set(s['horizon'] for s in available))
    horizon=cols[1].selectbox('予測期間',horizons or [1],format_func=lambda x:f'{x}営業日先')
    specs=sorted(set(s['version']+'|'+s['spec'] for s in available))
    spec=cols[2].selectbox('バージョン / 設定',['ALL']+specs,format_func=lambda x:'すべて' if x=='ALL' else x.replace('|',' / '))
    info=cols[3].selectbox('情報集合',['ALL','M1','M2','M3'],index=1,format_func=lambda x:{'ALL':'すべて','M1':'M1 / 市場','M2':'M2 / 為替・金利','M3':'M3 / マクロ'}[x])
    sample=cols[4].selectbox('比較サンプル',['matched','full'],format_func=lambda x:'共通銘柄' if x=='matched' else '各予測の全銘柄')
    model_options=sorted(set(s['model'] for s in available))
    selected=st.multiselect('表示モデル',model_options,default=[m for m in DEFAULT_MODELS if m in model_options] or model_options)
    c1,c2,c3=st.columns([2,2,1])
    benchmark=c1.selectbox('実績の比較対象',['UNIVERSE_EW']+data['benchmarks'].get(market,[]),format_func=lambda x:BENCH_NAMES.get(x,x))
    aggregation=c2.selectbox('平均予測の集計方法',['equal','weighted'],format_func=lambda x:'銘柄の単純平均' if x=='equal' else '当初の指数ウェイト固定（近似）')
    window=c3.selectbox('ローリング起点数',[20,40,60],index=0)
    series=[s for s in available if s['horizon']==horizon and (spec=='ALL' or s['version']+'|'+s['spec']==spec)
            and (info=='ALL' or s['information_set']==info) and s['model'] in selected]
    series=[s for s in series if get_points(s,sample)]
    if not series:
        st.info('この条件に比較可能な履歴はありません。版・情報集合・モデル・サンプルを切り替えてください。')
        return
    bases=sorted(set(s['return_basis'] for s in series))
    if len(bases)>1:
        basis=st.selectbox('比較するリターン定義',bases)
        series=[s for s in series if s['return_basis']==basis]
    if sample=='matched':
        periods=set.intersection(*[{(p['origin'],p['target']) for p in get_points(s,sample)} for s in series])
        series=[dict(s,points=[p for p in s['points'] if (p['origin'],p['target']) in periods]) for s in series]
        if not periods:
            st.info('選択した系列に共通する予測期間がまだありません。各予測の全銘柄モードでは個別履歴を確認できます。')
            return
        st.caption('直接比較：選択した全系列に共通する観測日・対象日・期間・リターン定義に限定しています。')
    primary=next((s for s in series if s['model']=='Ensemble' and s['information_set']=='M3'),next((s for s in series if s['model']=='Ensemble'),series[0]))
    primary_points=get_points(primary,sample)
    latest=primary_points[-1]
    mature=[p for p in primary_points if p['ready']][-window:]
    rmse=math.sqrt(sum(p['rmse']**2 for p in mature)/window) if len(mature)==window else None
    ic=sum(p['ic'] for p in mature)/window if len(mature)==window and all(valid(p['ic']) for p in mature) else None
    metrics=st.columns(4)
    metrics[0].metric('最新平均予測',fmt(prediction(latest,aggregation,benchmark)))
    metrics[1].metric(f'{window}起点 RMSE',fmt(rmse))
    metrics[2].metric(f'{window}起点 Rank IC',fmt(ic,False))
    metrics[3].metric('最新の実績照合',f"{latest['n_evaluated']} / {latest['n']}")
    st.caption('主表示：'+label(primary)+' ｜ '+latest['target']+' 対象')
    if aggregation=='weighted':
        st.info('当日の全構成銘柄・公表時点・リターン定義が揃った指数ウェイトのみ使用。途中の入替・除数変更を再現しない固定ウェイト近似です。揃わない系列は空欄になります。')
    versions=list(dict.fromkeys(s['version']+'|'+s['spec'] for s in series))
    figA=chart('A / 平均予測と市場実績')
    figB=chart('B / 同じ銘柄の平均予測誤差' if benchmark=='UNIVERSE_EW' and aggregation=='equal' else 'B / 市場リターンとの参考差',y_title='percentage points')
    reference={};table=[]
    for s in series:
        points=get_points(s,sample)
        dates=[p['target'] for p in points]
        values=[prediction(p,aggregation,benchmark) for p in points]
        add_line(figA,s,dates,[v*100 if valid(v) else None for v in values],versions)
        if benchmark=='UNIVERSE_EW' and s['id']==primary['id']:
            figA.add_trace(go.Scatter(x=dates,y=[100*p['actual'] if valid(p['actual']) else None for p in points],
                mode='lines',name='同じ銘柄の実績（主表示系列）',line=dict(color='#98a4aa',dash='dot'),visible=True))
        diffs=[]
        for p in points:
            actual=realized(p,benchmark)
            pred=p['paired_prediction'] if benchmark=='UNIVERSE_EW' and aggregation=='equal' else prediction(p,aggregation,benchmark)
            diffs.append(100*(pred-actual) if valid(actual) and valid(pred) else None)
            if valid(actual): reference[p['target']]=actual
            table.append(dict(market=market,version=s['version'],spec=s['spec'],information_set=s['information_set'],model=s['model'],horizon=horizon,
                origin=p['origin'],target=p['target'],prediction=prediction(p,aggregation,benchmark),actual=p['actual'],
                benchmark_actual=actual,rmse=p['rmse'],rank_ic=p['ic'],n=p['n'],n_evaluated=p['n_evaluated']))
        add_line(figB,s,dates,diffs,versions)
    if benchmark!='UNIVERSE_EW':
        dates=sorted(reference)
        figA.add_trace(go.Scatter(x=dates,y=[100*reference[d] for d in dates],name=BENCH_NAMES.get(benchmark,benchmark),
                                 line=dict(color='#979fa6',width=3)))
    figB.add_hline(y=0,line_color='#aab8bf',line_width=1)
    st.plotly_chart(figA,width='stretch',key='mean_chart')
    st.caption('横軸は予測対象営業日。価格指数と調整後株価の予測には、構成・ウェイト・配当の違いがあります。未実現値は空欄。')
    st.plotly_chart(figB,width='stretch',key='difference_chart')
    tabs=st.tabs(['個別株ランキング・実績照合','予測値と実現値','ローリング予測精度','モデル比較データ'])
    cross=[q for q in data['scatter'] if any(all(q[k]==s[k] for k in ['market','version','spec','information_set','horizon']) and any(p['origin']==q['origin'] and p['target']==q['target'] for p in s['points']) for s in series)]
    with tabs[0]:
        labels={s['id']:label(s) for s in series}
        sid=st.selectbox('ランキングの系列',list(labels),format_func=lambda x:labels.get(x,x),key=f'rank_series_{market}_{horizon}_{info}_{spec}')
        chosen=next(s for s in series if s['id']==sid)
        snapshots=[q for q in cross if all(q[k]==chosen[k] for k in ['market','version','spec','information_set','horizon'])]
        dates=sorted(set(q['target'] for q in snapshots),reverse=True)
        date=st.selectbox('ランキング対象営業日',dates,key='rankdate_'+sid)
        q=next(q for q in snapshots if q['target']==date)
        j=q['models'].index(chosen['model'])+3
        stock_rows=[dict(ticker=r[0],prediction=r[j],actual=r[1],error=r[j]-r[1] if valid(r[j]) and valid(r[1]) else None)
                    for r in q['rows'] if sample=='full' or r[2]]
        ranking=pd.DataFrame(stock_rows,columns=['ticker','prediction','actual','error']).sort_values('prediction',ascending=False).reset_index(drop=True)
        ranking.insert(0,'rank',ranking.index+1)
        display=ranking.copy()
        for col in ['prediction','actual','error']: display[col]*=100
        st.dataframe(display,width='stretch',hide_index=True,column_config={
            'rank':'順位','ticker':'銘柄','prediction':st.column_config.NumberColumn('予測 %',format='%.3f'),
            'actual':st.column_config.NumberColumn('実績 %',format='%.3f'),'error':st.column_config.NumberColumn('誤差 pp',format='%.3f')})
        st.caption(f"観測日 {q['origin']} → 対象日 {q['target']} ｜ 未実現は空欄。過去の断面は直近5実績日＋最新予測を収録。")
        st.download_button('ランキングCSVを保存',ranking.to_csv(index=False).encode('utf-8-sig'),file_name=('SYNTHETIC_' if demo else '')+'ranking.csv',mime='text/csv')
    with tabs[1]:
        scatter_date=st.selectbox('散布図の対象営業日',sorted(set(q['target'] for q in cross),reverse=True),key=f'scatterdate_{market}_{horizon}_{info}_{spec}')
        query=st.text_input('銘柄を絞り込み（任意）')
        figC=chart('C / 個別株の予測と実績')
        figC.update_layout(xaxis_title='予測リターン %',yaxis_title='実現リターン %')
        values=[]
        for s in series:
            q=next((q for q in cross if q['target']==scatter_date and all(q[k]==s[k] for k in ['market','version','spec','information_set','horizon'])),None)
            if q is None: continue
            j=q['models'].index(s['model'])+3
            rows=[r for r in q['rows'] if valid(r[1]) and valid(r[j]) and (sample=='full' or r[2]) and query.upper() in r[0].upper()]
            x=[100*r[j] for r in rows];y=[100*r[1] for r in rows];values.extend(x+y)
            figC.add_trace(go.Scatter(x=x,y=y,text=[r[0] for r in rows],name=label(s),mode='markers',
                                     marker=dict(size=5,opacity=.5,color=COLORS.get(s['model'])),
                                     hovertemplate='%{text}<br>予測 %{x:.2f}%<br>実績 %{y:.2f}%<extra></extra>'))
        if values:
            limits=[min(values),max(values)]
            figC.add_trace(go.Scatter(x=limits,y=limits,mode='lines',name='完全一致',line=dict(color='#aab4bb',dash='dot')))
        else: st.info('この対象日の実績はまだ保存されていません。成熟済みの日を選択してください。')
        st.plotly_chart(figC,width='stretch',key='scatter_chart')
    with tabs[2]:
        metric=st.radio('指標',['rmse','ic'],format_func=lambda x:'RMSE' if x=='rmse' else 'Rank IC',horizontal=True)
        figD=chart(f'D / {window}起点ローリング精度',y_title='%' if metric=='rmse' else 'Rank IC')
        for s in series:
            dates,values=rolling(get_points(s,sample),metric,window)
            add_line(figD,s,dates,values,versions)
        st.plotly_chart(figD,width='stretch',key='rolling_chart')
        st.caption(f'実績照合率95%以上の成熟済み起点だけを使用。{window}起点未満は表示を保留。RMSEは起点ごとの平均二乗誤差を集計します。')
    with tabs[3]:
        frame=pd.DataFrame(table)
        st.dataframe(frame,width='stretch',hide_index=True)
        st.download_button('比較履歴CSVを保存',frame.to_csv(index=False).encode('utf-8-sig'),file_name=('SYNTHETIC_' if demo else '')+'comparison.csv',mime='text/csv')
    with st.expander('集計定義・比較条件'):
        for note in data['notes']: st.write('・'+note)
        st.write('V1の学習・モデル仕様とV2のM1は別です。版ごとの成績差が、追加特徴量だけの因果効果を示すわけではありません。')
        st.write('V3〜V5は共通スキーマの実績が追加された時点で表示されます。未開発モデルの実績は作りません。')


dashboard()
