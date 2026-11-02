import time,sys,types,re
import pandas as pd, streamlit as st
pd.set_option('display.width',220); pd.set_option('display.max_columns',30)
from streamlit.testing.v1 import AppTest
cols=['symbol','tf','side','pattern','prox','sl','state','score']
def go(path,syms=''):
    st.cache_data.clear(); st.cache_resource.clear()
    t=time.time()
    at=AppTest.from_file(path,default_timeout=1800); at.run()
    if syms: at.text_input(key='in_symtxt').set_value(syms)
    at.button(key='btn_scan').click(); at.run()
    print(path,'secs',round(time.time()-t),'exc',[e.value[:300] for e in at.exception],'err',[e.value[:200] for e in at.error])
    return at
def rows(at): return at.session_state['scan']['rows'][cols].sort_values(cols[:5]).reset_index(drop=True)
print('now',pd.Timestamp.now(tz='Asia/Kolkata'))
ao=go('../old_app.py'); an=go('../new_dump.py')
a,b=rows(ao),rows(an)
print('FULL raw rows old/new',len(a),len(b),'IDENTICAL' if a.equals(b) else 'DIFF')
if not a.equals(b):
    d=pd.concat([a.assign(v='old'),b.assign(v='new')]).drop_duplicates(cols,keep=False); print(len(d)); print(d.to_string()[:3000])
for nm,x in (('old',ao),('new',an)):
    s=x.session_state['scan']
    print(nm,{t:(str(pd.Timestamp(s['bands3'][t]['ABB']['bar_time'],unit='ms',tz='Asia/Kolkata'))[:10],round(s['bands3'][t]['ABB']['low']),round(s['bands3'][t]['ABB']['high'])) for t in ('1d','1w','1M')})
f=pd.read_pickle('/tmp/f.pkl')
print('shown',len(f),'tv',f.tv_ok.value_counts().to_dict(),'mcap blank',int(f.mcap_cr.isna().sum()),'caps',f.symbol.nunique())
print(f[['symbol','tf','side','prox','sl','tv_ok','state','mcap_cr']].head(8).to_string())
print('=== BAJAJ TV')
sys.modules['streamlit']=__import__('unittest.mock').mock.MagicMock()
src=re.sub(r'\nmain\(\)\s*$','\n',open('app.py',encoding='utf-8').read())
m=types.ModuleType('app'); m.__file__='app.py'; sys.modules['app']=m; exec(compile(src,'app.py','exec'),m.__dict__)
I=m.PineInputs().sanitized()
for sy in ['BAJAJ-AUTO','NAM-INDIA','M&M']:
    print(sy,'cash',m.tv_cash_zones(sy,'4h',I) is not None,'fut',None if m.tv_future_frame(sy,'4h') is None else len(m.tv_future_frame(sy,'4h')))
