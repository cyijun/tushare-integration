import os
import json
import requests
import pandas as pd

url = 'https://api.tushare.pro'
token = os.environ['TUSHARE_TOKEN']

payload = {
    'api_name': 'bak_basic',
    'token': token,
    'params': {'trade_date': '20181120'},
    'fields': ''
}

resp = requests.post(url, json=payload).json()
df = pd.DataFrame(data=resp['data']['items'], columns=resp['data']['fields'])

# 直接 to_datetime 看哪些变 NaT
s = df['list_date']
d = pd.to_datetime(s, format='mixed', errors='coerce')
nat_mask = d.isna()
print(f"NaT count: {nat_mask.sum()}")
print(f"\n导致 NaT 的原始 list_date 值:")
print(s[nat_mask].unique())
print(f"\n对应的行:")
print(df[nat_mask][['ts_code', 'name', 'list_date']])

# 再看看这些值的全量分布
print(f"\n--- list_date 值长度分布 ---")
print(s.astype(str).str.len().value_counts().sort_index())

print(f"\n--- 列出所有非8位长度的值 ---")
wrong_len = s.astype(str).str.len() != 8
print(s[wrong_len].unique())
