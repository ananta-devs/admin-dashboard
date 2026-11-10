"""VIBE Analytics and Reporting background tasks."""

from app.tasks.reporting import execute_report_run

__all__ = ["execute_report_run"]
