import sqlite3
conn = sqlite3.connect('extraction.db')
rows = conn.execute("SELECT name, customer_name, json_extract(json_data, '$.customer_file') as json_customer FROM template WHERE is_active=1").fetchall()
for r in rows:
    print(r)
conn.close()