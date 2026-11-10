import psycopg

def wipe_records(dbname):
    print(f"Connecting to {dbname}...")
    conn = psycopg.connect(f"postgresql://vibe_admin:dev_secret_postgres_pass_change_in_prod@127.0.0.1:5432/{dbname}")
    conn.autocommit = True
    cur = conn.cursor()

    cur.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'public';")
    tables = [r[0] for r in cur.fetchall()]
    
    # Exclude alembic_version and permissions (system definitions)
    tables_to_truncate = [t for t in tables if t not in ('alembic_version', 'permissions')]
    print(f"Found {len(tables_to_truncate)} tables to truncate in {dbname}...")

    joined = ", ".join(f'"{t}"' for t in tables_to_truncate)
    truncate_query = f"TRUNCATE TABLE {joined} RESTART IDENTITY CASCADE;"
    cur.execute(truncate_query)
    print(f"Truncated {len(tables_to_truncate)} tables in {dbname}.")

    # Count remaining
    remaining = {}
    for t in tables:
        cur.execute(f'SELECT count(*) FROM "{t}";')
        remaining[t] = cur.fetchone()[0]

    total = sum(remaining.values())
    print(f"Total remaining records in {dbname}: {total}")
    for t, c in remaining.items():
        if c > 0:
            print(f"  {t}: {c}")

    conn.close()

wipe_records("vibe_enterprise")
