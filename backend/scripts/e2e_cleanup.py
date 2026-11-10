"""Stage 12.2 Scoped E2E Database and Redis Cleanup Engine.

Provides fail-closed, run-scoped cleanup of synthetic test data targeting
exclusively `vibe_enterprise_test` and Redis DB 15.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid

# Append backend root to sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import redis
from sqlalchemy import create_engine, text


def get_sync_db_url(db_name: str = "vibe_enterprise_test") -> str:
    user = os.environ.get("POSTGRES_USER", "vibe_admin")
    password = os.environ.get("POSTGRES_PASSWORD", "dev_secret_postgres_pass_change_in_prod")
    host = os.environ.get("POSTGRES_HOST", "127.0.0.1")
    port = os.environ.get("POSTGRES_PORT", "5432")
    return f"postgresql+psycopg://{user}:{password}@{host}:{port}/{db_name}"


def cleanup_run(
    run_id: str,
    org_ids: list[str] | None = None,
    user_ids: list[str] | None = None,
    expected_db: str = "vibe_enterprise_test",
    redis_db: int = 15,
    db_url: str | None = None,
) -> dict[str, int]:
    """Execute fail-closed, exact-identifier scoped cleanup for a specific E2E test run."""
    if not run_id or not run_id.strip():
        raise ValueError("FATAL: run_id must be provided for scoped cleanup.")

    url = db_url or os.environ.get("DATABASE_SYNC_URL") or get_sync_db_url(expected_db)
    engine = create_engine(url, pool_pre_ping=True)

    counts = {
        "sessions_deleted": 0,
        "tokens_deleted": 0,
        "orgs_deleted": 0,
        "audit_events_deleted": 0,
        "users_deleted": 0,
        "redis_keys_deleted": 0,
    }

    with engine.connect() as conn:
        # 1. Fail-closed database identity verification
        actual_db = conn.execute(text("SELECT current_database();")).scalar()
        if actual_db != expected_db:
            raise RuntimeError(
                f"FATAL REFUSAL: Cleanup target mismatch! Expected '{expected_db}', but connected to '{actual_db}'. "
                "Aborting cleanup immediately without executing any DELETE."
            )
        if actual_db in ("vibe_enterprise", "vibe_benchmark", "postgres"):
            raise RuntimeError(
                f"FATAL REFUSAL: Cleanup invoked against protected database '{actual_db}'! Aborting."
            )

        # 2. Resolve target user IDs and org IDs
        resolved_user_ids = [uuid.UUID(u) for u in (user_ids or [])]
        resolved_org_ids = [uuid.UUID(o) for o in (org_ids or [])]

        # If IDs were not explicitly passed, query by exact run_id patterns in test DB
        if not resolved_org_ids:
            res_orgs = conn.execute(
                text("SELECT id FROM organizations WHERE slug LIKE :pat"),
                {"pat": f"%{run_id}%"},
            ).fetchall()
            resolved_org_ids = [row[0] for row in res_orgs]

        if not resolved_user_ids:
            res_users = conn.execute(
                text("SELECT id FROM users WHERE email LIKE :pat"),
                {"pat": f"%{run_id}%"},
            ).fetchall()
            resolved_user_ids = [row[0] for row in res_users]

        # Step 1: Delete authentication sessions & action tokens for exact test users
        if resolved_user_ids:
            del_sess = conn.execute(
                text("DELETE FROM authentication_sessions WHERE user_id = ANY(:user_ids)"),
                {"user_ids": resolved_user_ids},
            )
            counts["sessions_deleted"] = del_sess.rowcount

            del_tok = conn.execute(
                text("DELETE FROM authentication_action_tokens WHERE user_id = ANY(:user_ids)"),
                {"user_ids": resolved_user_ids},
            )
            counts["tokens_deleted"] = del_tok.rowcount

        # Step 2: Delete audit records belonging ONLY to exact test users or test orgs
        # STRICTLY NEVER: organization_id IS NULL
        if resolved_user_ids or resolved_org_ids:
            clauses = []
            params: dict[str, list[uuid.UUID]] = {}
            if resolved_user_ids:
                clauses.append("actor_user_id = ANY(:user_ids)")
                params["user_ids"] = resolved_user_ids
            if resolved_org_ids:
                clauses.append("organization_id = ANY(:org_ids)")
                params["org_ids"] = resolved_org_ids

            sql_audit = f"DELETE FROM administrative_audit_events WHERE {' OR '.join(clauses)}"
            del_audit = conn.execute(text(sql_audit), params)
            counts["audit_events_deleted"] = del_audit.rowcount

        # Step 3: Delete organizations (cascades to workspaces, workbooks, sheets, cells, dashboards, widgets, jobs, reports, roles, memberships)
        if resolved_org_ids:
            del_orgs = conn.execute(
                text("DELETE FROM organizations WHERE id = ANY(:org_ids)"),
                {"org_ids": resolved_org_ids},
            )
            counts["orgs_deleted"] = del_orgs.rowcount

        # Step 4: Delete test users (cascades to user_credentials and mfa_credentials)
        if resolved_user_ids:
            del_users = conn.execute(
                text("DELETE FROM users WHERE id = ANY(:user_ids)"),
                {"user_ids": resolved_user_ids},
            )
            counts["users_deleted"] = del_users.rowcount

        conn.commit()

    # 3. Redis DB 15 Scoped Cleanup (Only keys matching e2e:<runId>:*)
    try:
        redis_password = os.environ.get("REDIS_PASSWORD", "dev_secret_redis_pass_change_in_prod")
        redis_host = os.environ.get("REDIS_HOST", "127.0.0.1")
        redis_port = int(os.environ.get("REDIS_PORT", "6379"))
        r = redis.Redis(
            host=redis_host,
            port=redis_port,
            password=redis_password,
            db=redis_db,
            decode_responses=True,
        )
        keys = r.keys(f"e2e:{run_id}:*")
        if keys:
            deleted_keys = r.delete(*keys)
            counts["redis_keys_deleted"] = deleted_keys
        r.close()
    except Exception as re:
        print(f"Warning: Redis cleanup encountered non-fatal error: {re}", file=sys.stderr)

    return counts


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Scoped E2E Test Cleanup")
    parser.add_argument("--run-id", required=True, help="Unique E2E test run ID")
    parser.add_argument("--org-ids", nargs="*", default=[], help="Exact organization UUIDs")
    parser.add_argument("--user-ids", nargs="*", default=[], help="Exact user UUIDs")
    parser.add_argument(
        "--expected-db", default="vibe_enterprise_test", help="Expected database name"
    )
    parser.add_argument("--redis-db", type=int, default=15, help="Redis DB index")
    args = parser.parse_args()

    res = cleanup_run(
        run_id=args.run_id,
        org_ids=args.org_ids,
        user_ids=args.user_ids,
        expected_db=args.expected_db,
        redis_db=args.redis_db,
    )
    print(json.dumps(res, indent=2))
