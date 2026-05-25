import os
import json
import requests
import pandas as pd

url = 'https://api.tushare.pro'
token = os.environ['TUSHARE_TOKEN']

# 那 6 只股票的代码
codes = ['002943.SZ', '601860.SH', '300752.SZ', '002942.SZ', '002941.SZ', '603187.SH']

# 先用 stock_basic 查真实上市日期
payload = {
    'api_name': 'stock_basic',
    'token': token,
    'params': {},
    'fields': 'ts_code,name,list_date'
}
resp = requests.post(url, json=payload).json()
df_basic = pd.DataFrame(data=resp['data']['items'], columns=resp['data']['fields'])
print("=== stock_basic 中的 list_date ===")
print(df_basic[df_basic['ts_code'].isin(codes)][['ts_code', 'name', 'list_date']].to_string(index=False))

# 再用 bak_basic 查 20181120 的数据
payload2 = {
    'api_name': 'bak_basic',
    'token': token,
    'params': {'trade_date': '20181120'},
    'fields': ''
}
resp2 = requests.post(url, json=payload2).json()
df_bak = pd.DataFrame(data=resp2['data']['items'], columns=resp2['data']['fields'])
print("\n=== bak_basic(20181120) 中的 list_date ===")
print(df_bak[df_bak['ts_code'].isin(codes)][['ts_code', 'name', 'list_date']].to_string(index=False))

# 再看看 new_share 中这些股票的上市日期
payload3 = {
    'api_name': 'new_share',
    'token': token,
    'params': {},
    'fields': ''
}
resp3 = requests.post(url, json=payload3).json()
df_new = pd.DataFrame(data=resp3['data']['items'], columns=resp3['data']['fields'])
print("\n=== new_share 中的上市日期 ===")
print(df_new[df_new['ts_code'].isin(codes)][['ts_code', 'name', 'ipo_date', 'list_date']].to_string(index=False))
