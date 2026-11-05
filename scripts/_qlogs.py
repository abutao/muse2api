import sqlite3
c=sqlite3.connect("data/muse-proxy.db")
c.row_factory=sqlite3.Row
print("tables", [r[0] for r in c.execute("select name from sqlite_master where type='table'").fetchall()])
rows=c.execute("select id,created_at,method,path,api_type,model,status,source,prompt_preview,upstream_task_id,substr(coalesce(error,''),1,160) err from call_logs order by id desc limit 40").fetchall()
print("n=", len(rows))
for r in rows:
    print(dict(r))
