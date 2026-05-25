import yaml
import pandas as pd
import clickhouse_connect

with open('config.yaml', 'r') as f:
    cfg = yaml.safe_load(f)

db = cfg['database']
client = clickhouse_connect.get_client(
    host=db.get('host', 'localhost'),
    port=db.get('port', 8123),
    username=db.get('user', 'default'),
    password=db.get('password', ''),
    database=db['db_name']
)

# 1. 建测试表：Date32 默认不允许 NULL
client.command("DROP TABLE IF EXISTS test_date_null")
client.command("CREATE TABLE test_date_null (id Int32, d Date32) ENGINE = MergeTree() ORDER BY id")

# 2. 插入 None
df = pd.DataFrame({'id': [1, 2], 'd': [pd.Timestamp('2020-01-01').date(), None]})
print("DataFrame:\n", df)
print("d dtype:", df['d'].dtype)
try:
    client.insert_df('test_date_null', df)
    print("Insert None succeeded")
    print(client.query("SELECT * FROM test_date_null").result_set)
except Exception as e:
    print(f"Insert None failed: {type(e).__name__}: {e}")

# 3. 建测试表：Nullable(Date32)
client.command("DROP TABLE IF EXISTS test_date_nullable")
client.command("CREATE TABLE test_date_nullable (id Int32, d Nullable(Date32)) ENGINE = MergeTree() ORDER BY id")

df2 = pd.DataFrame({'id': [1, 2], 'd': [pd.Timestamp('2020-01-01').date(), None]})
try:
    client.insert_df('test_date_nullable', df2)
    print("\nInsert None into Nullable(Date32) succeeded")
    print(client.query("SELECT * FROM test_date_nullable").result_set)
except Exception as e:
    print(f"Insert None into Nullable failed: {type(e).__name__}: {e}")

# 4. 插入 pd.NaT
df3 = pd.DataFrame({'id': [3], 'd': [pd.NaT]})
try:
    client.insert_df('test_date_nullable', df3)
    print("\nInsert NaT into Nullable(Date32) succeeded")
    print(client.query("SELECT * FROM test_date_nullable WHERE id=3").result_set)
except Exception as e:
    print(f"Insert NaT into Nullable failed: {type(e).__name__}: {e}")

# 清理
client.command("DROP TABLE IF EXISTS test_date_null")
client.command("DROP TABLE IF EXISTS test_date_nullable")
