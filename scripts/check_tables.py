import psycopg

dbs = ['vibe_enterprise', 'vibe_enterprise_test', 'vibe_benchmark']
for db in dbs:
    try:
        conn = psycopg.connect(f'postgresql://vibe_admin:dev_secret_postgres_pass_change_in_prod@127.0.0.1:5432/{db}')
        cur = conn.cursor()
        cur.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'public';")
        tables = [r[0] for r in cur.fetchall()]
        table_counts = {}
        for t in tables:
            cur.execute(f'SELECT count(*) FROM "{t}";')
            table_counts[t] = cur.fetchone()[0]
        total = sum(table_counts.values())
        print(f"=== {db} (Total: {total} records across {len(tables)} tables) ===")
        for t, cnt in sorted(table_counts.items(), key=lambda x: -x[1]):
            if cnt > 0:
                print(f"  {t}: {cnt}")
        conn.close()
    except Exception as e:
        print(f"Error checking {db}: {e}")
