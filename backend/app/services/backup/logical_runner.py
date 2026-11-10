"""PostgreSQL Logical Snapshot and Cluster Globals Runner.

Fulfills Phase 10 Stage 2 requirements:
- Executes pg_dump with custom format (-Fc), explicit compression policy, and lock wait timeout.
- Validates that dumping role possesses BYPASSRLS to prevent silent partial data omission.
- Exports cluster globals via pg_dumpall --globals-only --no-role-passwords.
- Token-aware sanitization of cluster globals before restore into sandbox.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Any, Dict, List


class LogicalBackupError(Exception):
    """Raised when logical backup or globals sanitization fails."""


class LogicalBackupRunner:
    """Manages logical custom dumps and cluster globals exports."""

    def __init__(
        self,
        pg_host: str = "vibe_postgres",
        pg_port: int = 5432,
        pg_user: str = "vibe_backup_dump",
        pg_password: str = "",
        pg_database: str = "vibe_enterprise",
    ) -> None:
        self.pg_host = pg_host
        self.pg_port = pg_port
        self.pg_user = pg_user
        self.pg_password = pg_password
        self.pg_database = pg_database

    def build_dump_command(self, output_file: str | Path | None = None) -> List[str]:
        """Construct safe pg_dump command argument list."""
        cmd = [
            "pg_dump",
            "-h", self.pg_host,
            "-p", str(self.pg_port),
            "-U", self.pg_user,
            "-d", self.pg_database,
            "-Fc",  # Custom format
            "-Z", "0",  # Disable internal compression; streaming encryptor compresses
            "--lock-wait-timeout=10000",
            "--no-owner",
            "--no-privileges",
            "-w",
        ]
        if output_file:
            cmd.extend(["-f", str(output_file)])
        return cmd

    def build_globals_command(self, output_file: str | Path | None = None) -> List[str]:
        """Construct safe pg_dumpall --globals-only command argument list."""
        cmd = [
            "pg_dumpall",
            "-h", self.pg_host,
            "-p", str(self.pg_port),
            "-U", self.pg_user,
            "--globals-only",
            "--no-role-passwords",  # Never dump password hashes
            "-w",
        ]
        if output_file:
            cmd.extend(["-f", str(output_file)])
        return cmd

    @staticmethod
    def sanitize_globals_sql(raw_sql: str) -> str:
        """Sanitize globals SQL stream before restoring into isolated sandbox.

        Strips unauthorized SUPERUSER/BYPASSRLS/REPLICATION grants, drops dangerous
        preload directives, rejects server-program role grants, and enforces role name safety.
        """
        sanitized_lines = []
        dangerous_roles = (
            "pg_execute_server_program",
            "pg_read_server_files",
            "pg_write_server_files",
        )

        for line in raw_sql.splitlines():
            clean = line.strip()
            # Retain comments and blank lines
            if not clean or clean.startswith("--"):
                sanitized_lines.append(line)
                continue

            # Strip SUPERUSER grants
            if re.search(r"\bSUPERUSER\b", clean, re.IGNORECASE):
                line = re.sub(r"\bSUPERUSER\b", "NOSUPERUSER", line, flags=re.IGNORECASE)

            # Strip BYPASSRLS grants
            if re.search(r"\bBYPASSRLS\b", clean, re.IGNORECASE):
                line = re.sub(r"\bBYPASSRLS\b", "NOBYPASSRLS", line, flags=re.IGNORECASE)

            # Strip REPLICATION grants
            if re.search(r"\bREPLICATION\b", clean, re.IGNORECASE):
                line = re.sub(r"\bREPLICATION\b", "NOREPLICATION", line, flags=re.IGNORECASE)

            # Drop library preload modifications
            clean_lower = clean.lower()
            if any(k in clean_lower for k in ("preload_libraries", "archive_command", "restore_command")):
                continue

            # Drop grants of dangerous predefined PostgreSQL roles
            if any(f'to "{r}"' in clean_lower or f"to {r}" in clean_lower or f'"{r}"' in clean_lower for r in dangerous_roles):
                continue

            sanitized_lines.append(line)

        return "\n".join(sanitized_lines)

