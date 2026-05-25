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

# 模拟修复后的 FillNAPipeline
column = {'name': 'list_date', 'data_type': 'date', 'default': '1970-01-01'}
col_data = df['list_date'].replace({pd.NaT: None})
if column['data_type'] in ('date', 'datetime'):
    col_data = col_data.replace({'': None, '0': None, 0: None})
col_data = col_data.fillna(column['default'])

print(f"After FillNAPipeline fix, null-like count: {col_data.isna().sum()}")
print(f"Value '0' count: {(col_data == 0).sum()}")
print(f"Value '0' count (str): {(col_data == '0').sum()}")

# 模拟 TransformDTypePipeline
d = pd.to_datetime(col_data, format='mixed', errors='coerce').dt.date
print(f"After TransformDTypePipeline, NaT count: {d.isna().sum()}")
print(f"All unique values include NaT: {pd.isna(d).any()}")

# 确认那 6 条记录现在的值
print(f"\n原来为 0 的 6 条记录现在的 list_date:")
original_zeros = df['list_date'] == 0
print(d[original_zeros].unique())
