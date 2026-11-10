"""CLI entrypoint for executing a single analytics job in a standalone OS subprocess."""

import asyncio
import os
import sys

from app.tasks.analytics import _execute_analytics_job_async


def main() -> None:
    if len(sys.argv) < 2:
        sys.stderr.write("Usage: python -m app.tasks.run_worker_job <job_id> [celery_task_id]\n")
        sys.exit(1)

    job_id = sys.argv[1]
    celery_task_id = sys.argv[2] if len(sys.argv) > 2 else f"proc-{os.getpid()}"

    res = asyncio.run(_execute_analytics_job_async(job_id, celery_task_id))
    print(f"SUCCESS: {res}")


if __name__ == "__main__":
    main()
