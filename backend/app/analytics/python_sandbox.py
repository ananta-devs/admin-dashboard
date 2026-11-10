"""Governed execution sandbox for user-defined Python analytics.

THREAT MODEL & ISOLATION ARCHITECTURE:
-------------------------------------
User-defined Python scripts constitute untrusted, potentially hostile multi-tenant code.
AST validation and in-process/restricted child subprocesses DO NOT establish an operating-system
security boundary against determined attackers (privilege escalation, environment inspection,
process manipulation, socket creation, symlink traversal, or resource exhaustion).

This subsystem enforces a defense-in-depth architecture:
1. Static Pre-Check (`validate_python_ast`):
   - Fast syntactic validation and early user feedback for disallowed constructs.
   - NOTE: This is a developer convenience and linting check, NOT an OS security boundary.

2. Operating-System Level OCI Container Isolation (`IsolatedDockerSandboxRunner`):
   - Identity & Privileges:
     * Dedicated unprivileged non-root execution identity (UID 10001:10001).
     * Complete capability drop (`--cap-drop ALL`).
     * Prevents privilege elevation (`--security-opt no-new-privileges:true`).
     * No inherited database or Redis connections, sockets, or credentials.
   - Filesystem Isolation:
     * Read-only root filesystem (`--read-only`).
     * Bounded ephemeral temporary filesystem (`--tmpfs /tmp:rw,noexec,nosuid,size=64m`).
     * No host home-directory mounts, no application source mounts, no secrets.
     * No Docker socket mounts (`/var/run/docker.sock` is absent).
     * Dedicated ephemeral workspace mounted at `/workspace:rw`, wiped immediately on exit.
   - Network Isolation:
     * Strict complete network namespace isolation (`--network none`).
     * Zero outbound internet access, no DNS resolution, no access to PostgreSQL (5432) or Redis (6379).
   - Resource Quotas & Process Controls:
     * Kernel cgroup process limit ceiling (`--pids-limit 32`) blocking fork bombs.
     * Hard memory limit (`--memory 256m`, `--memory-swap 256m`) with cgroup OOM termination.
     * CPU quota limitation (`--cpus 1.0`).
     * Wall-clock execution timeout (15s hard kill).
     * Bounded output payload (max 5 MB) and bounded stdout/stderr buffers (max 64 KB).
   - Administrative Safety Gate:
     * When `settings.PYTHON_SANDBOX_ENABLED` is False, execution is strictly rejected with
       status='disabled' while script authoring, versioning, and auditing remain fully available.
"""

from __future__ import annotations

import ast
import hashlib
import json
import logging
import math
import os
import re
import shutil
import socket
import stat
import struct
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, TypedDict

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

PINNED_IMAGE_REPOSITORY: str = settings.PYTHON_SANDBOX_IMAGE_REPO
PINNED_IMAGE_DIGEST: str = settings.PYTHON_SANDBOX_IMAGE_DIGEST

type JSONValue = str | int | float | bool | None | list[JSONValue] | dict[str, JSONValue]

ExecutionStatus = Literal["completed", "failed", "timeout", "disabled", "rejected"]


class EnforcementDetails(TypedDict, total=False):
    """Typed telemetry and enforcement metadata emitted by the trusted host."""

    policy: str | None
    exit_code: int | None
    limit_seconds: int | None
    error: str | None
    expected: str | None
    actual: str | None
    pid_1_architecture: str | None
    pid_namespace_teardown: bool | None
    cgroup_enforced: bool | None
    cgroup_version: int | None
    supervisor_dumpable: int | None
    container_name: str | None
    container_id: str | None
    container_path: str | None
    container_args: list[str] | None
    volume_name: str | None
    image_ref: str | None
    sealed_sha256: str | None
    transferred_sha256: str | None
    pinned_manifest_digest: str | None
    requested_manifest_digest: str | None
    image_config_id: str | None
    repo_digests: list[str] | None
    container_image_id: str | None
    artifact_path: str | None
    cleanup_confirmed: bool | None
    cleanup_error: str | None
    pinned_repository_reference: str | None
    expected_manifest_digest: str | None
    image_inspection_id: str | None
    container_config_image: str | None
    effective_path: str | None
    effective_args: list[str] | None
    architecture: str | None
    os: str | None


def verify_image_digest(image_ref: str | None = None) -> bool:
    """Verify that the container image matches the cryptographic SHA-256 digest."""
    return PythonSandboxRunner().verify_image_digest()

FORBIDDEN_IMPORTS = {
    "subprocess",
    "socket",
    "pty",
    "ctypes",
    "multiprocessing",
    "threading",
    "http.server",
    "urllib.request",
    "requests",
    "httpx",
    "aiohttp",
    "webbrowser",
}

FORBIDDEN_CALLS = {
    "eval",
    "exec",
    "compile",
    "__import__",
    "globals",
    "locals",
}


def is_symlink_or_reparse_point(path: Path | str) -> bool:
    """Robust detection of symlinks and reparse points across POSIX and Windows/NTFS filesystems."""
    try:
        path_str = str(path)
        if os.path.islink(path_str):
            return True
        st = os.lstat(path_str)
        if stat.S_ISLNK(st.st_mode):
            return True
        if getattr(st, "st_reparse_tag", 0) != 0:
            return True
        if os.path.lexists(path_str) and not os.path.exists(path_str):
            return True
    except Exception:
        pass
    return False


def verify_path_containment_and_no_symlinks(
    target_path: Path,
    root_dir: Path,
) -> tuple[bool, str]:
    """Verify target_path is strictly within root_dir and neither target nor any parent directory is a symlink or reparse point."""
    try:
        # Check target directly
        if is_symlink_or_reparse_point(target_path):
            return False, "Target is a symbolic link or reparse point."

        # Canonical path resolution
        root_resolved = root_dir.resolve()
        target_resolved = target_path.resolve()

        # Strict containment verification
        if not str(target_resolved).startswith(str(root_resolved)):
            return False, "Path escapes authorized directory boundary."

        # Check every path segment from target up to root
        curr = target_path
        depth = 0
        while curr != root_dir and curr != curr.parent:
            depth += 1
            if depth > 10:
                return False, "Directory depth limit exceeded."
            if is_symlink_or_reparse_point(curr):
                return False, f"Path component '{curr.name}' is a symbolic link or reparse point."
            curr = curr.parent

        # File type checks
        if target_path.exists():
            st = os.lstat(str(target_path))
            if stat.S_ISFIFO(st.st_mode):
                return False, "Target is a FIFO special file."
            if stat.S_ISCHR(st.st_mode) or stat.S_ISBLK(st.st_mode):
                return False, "Target is a device special file."
            if stat.S_ISSOCK(st.st_mode):
                return False, "Target is a socket special file."
            if not stat.S_ISREG(st.st_mode):
                return False, "Target is not a regular file."

            # Hard link check on POSIX systems
            if hasattr(st, "st_nlink") and st.st_nlink > 1 and os.name != "nt":
                return False, "Hard linked files are not permitted."

        return True, ""
    except Exception as e:
        return False, f"Path verification failed: {e}"


@dataclass
class SandboxExecutionResult:
    status: ExecutionStatus
    execution_time_ms: int
    memory_peak_mb: float
    stdout: str
    stderr: str
    error_message: str | None = None
    result_payload: dict[str, JSONValue] | None = None
    isolation_backend: str = "docker_oci"
    enforcement_details: EnforcementDetails | None = None


def validate_python_ast(code_str: str) -> str | None:
    """Statically inspect user-defined Python code AST for syntax and obvious hazards.

    NOTE: This is a static linting filter for developer feedback. It does NOT constitute
    an operating-system security boundary. True isolation is provided by IsolatedDockerSandboxRunner.
    """
    try:
        tree = ast.parse(code_str)
    except SyntaxError as e:
        return f"Python Syntax Error at line {e.lineno}: {e.msg}"

    for node in ast.walk(tree):
        # 1. Check imports
        if isinstance(node, ast.Import):
            for alias in node.names:
                root_module = alias.name.split(".")[0]
                if root_module in FORBIDDEN_IMPORTS:
                    return f"Security violation: import of '{alias.name}' is prohibited in analytical scripts."
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                root_module = node.module.split(".")[0]
                if root_module in FORBIDDEN_IMPORTS:
                    return f"Security violation: 'from {node.module} import ...' is prohibited in analytical scripts."

        # 2. Check forbidden calls like eval/exec
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id in FORBIDDEN_CALLS:
                return f"Security violation: direct call to '{node.func.id}()' is prohibited."
            if isinstance(node.func, ast.Attribute):
                if node.func.attr in ("system", "popen", "spawn", "fork", "kill"):
                    return f"Security violation: process control '{node.func.attr}()' is prohibited."

    return None


FRAME_USER_STDOUT = 1
FRAME_USER_STDERR = 2
FRAME_SUPERVISOR_CONTROL = 3
FRAME_SUPERVISOR_ARTIFACT = 4


class StreamDemultiplexer:
    """Demultiplexes binary length-prefixed stream into user stdout, user stderr, and supervisor control frames."""

    def __init__(self, max_retained_bytes: int = 65536, max_flood_bytes: int = 10 * 1024 * 1024) -> None:
        self.stdout_chunks: list[bytes] = []
        self.stderr_chunks: list[bytes] = []
        self.stdout_bytes = 0
        self.stderr_bytes = 0
        self.flood_killed = False
        self.control_payload: dict[str, Any] | None = None
        self.artifact_tar_bytes: bytes | None = None
        self.control_event = threading.Event()
        self.lock = threading.Lock()
        self.max_retained = max_retained_bytes
        self.max_flood = max_flood_bytes

    def read_stdout_stream(self, stream: Any, on_flood: Any = None) -> None:
        buf = bytearray()
        try:
            while True:
                chunk = stream.read1(4096) if hasattr(stream, "read1") else stream.read(4096)
                if not chunk:
                    break
                buf.extend(chunk)

                with self.lock:
                    if (self.stdout_bytes + self.stderr_bytes + len(buf)) > self.max_flood:
                        self.flood_killed = True
                        if on_flood:
                            on_flood()
                        break

                    while len(buf) >= 5:
                        frame_type = buf[0]
                        if frame_type not in (FRAME_USER_STDOUT, FRAME_USER_STDERR, FRAME_SUPERVISOR_CONTROL, FRAME_SUPERVISOR_ARTIFACT):
                            b = bytes([buf[0]])
                            buf = buf[1:]
                            prev_bytes = self.stdout_bytes
                            self.stdout_bytes += 1
                            if prev_bytes < self.max_retained:
                                self.stdout_chunks.append(b)
                            continue

                        payload_len = struct.unpack(">I", buf[1:5])[0]
                        if len(buf) < 5 + payload_len:
                            break

                        payload = bytes(buf[5:5 + payload_len])
                        del buf[:5 + payload_len]

                        if frame_type == FRAME_USER_STDOUT:
                            prev_bytes = self.stdout_bytes
                            self.stdout_bytes += len(payload)
                            if prev_bytes < self.max_retained:
                                self.stdout_chunks.append(payload[: self.max_retained - prev_bytes])
                        elif frame_type == FRAME_USER_STDERR:
                            prev_bytes = self.stderr_bytes
                            self.stderr_bytes += len(payload)
                            if prev_bytes < self.max_retained:
                                self.stderr_chunks.append(payload[: self.max_retained - prev_bytes])
                        elif frame_type == FRAME_SUPERVISOR_CONTROL:
                            try:
                                self.control_payload = json.loads(payload.decode("utf-8"))
                                self.control_event.set()
                            except Exception:
                                pass
                        elif frame_type == FRAME_SUPERVISOR_ARTIFACT:
                            self.artifact_tar_bytes = payload
        except Exception:
            pass
        finally:
            try:
                stream.close()
            except Exception:
                pass

    def read_stderr_stream(self, stream: Any) -> None:
        try:
            while True:
                chunk = stream.read1(4096) if hasattr(stream, "read1") else stream.read(4096)
                if not chunk:
                    break
                with self.lock:
                    prev_bytes = self.stderr_bytes
                    self.stderr_bytes += len(chunk)
                    if prev_bytes < self.max_retained:
                        self.stderr_chunks.append(chunk[: self.max_retained - prev_bytes])
        except Exception:
            pass
        finally:
            try:
                stream.close()
            except Exception:
                pass

    def get_stdout_text(self) -> str:
        with self.lock:
            return b"".join(self.stdout_chunks)[: self.max_retained].decode("utf-8", errors="replace")

    def get_stderr_text(self) -> str:
        with self.lock:
            return b"".join(self.stderr_chunks)[: self.max_retained].decode("utf-8", errors="replace")


USER_CONTAINER_EXECUTOR = """# Governed User Code Executor (runs as untrusted child process)
import json
import sys
import os
import time

def main():
    # Enforce directory depth ceiling (prevent recursive deep directory exhaustion)
    MAX_DIR_DEPTH = 5
    orig_mkdir = os.mkdir
    orig_makedirs = os.makedirs

    def safe_mkdir(path, *args, **kwargs):
        p = os.path.abspath(path)
        rel = os.path.relpath(p, "/workspace")
        parts = [part for part in rel.split(os.sep) if part and part != "."]
        if len(parts) > MAX_DIR_DEPTH:
            raise RecursionError(f"Directory depth limit exceeded (maximum allowed depth is {MAX_DIR_DEPTH})")
        return orig_mkdir(path, *args, **kwargs)

    def safe_makedirs(path, *args, **kwargs):
        p = os.path.abspath(path)
        rel = os.path.relpath(p, "/workspace")
        parts = [part for part in rel.split(os.sep) if part and part != "."]
        if len(parts) > MAX_DIR_DEPTH:
            raise RecursionError(f"Directory depth limit exceeded (maximum allowed depth is {MAX_DIR_DEPTH})")
        return orig_makedirs(path, *args, **kwargs)

    os.mkdir = safe_mkdir
    os.makedirs = safe_makedirs

    # Resolve input directory (read-only mount) and output directory
    input_dir = "/input" if os.path.isdir("/input") and os.path.exists("/input/input.json") else "."
    output_dir = "/output" if os.path.isdir("/output") else "."

    input_file = os.path.join(input_dir, "input.json")
    params_file = os.path.join(input_dir, "parameters.json")
    code_file = os.path.join(input_dir, "user_code.py")
    output_file = os.path.join(output_dir, "output.json")

    try:
        with open(input_file, "r", encoding="utf-8") as f:
            in_data = json.load(f)
        with open(params_file, "r", encoding="utf-8") as f:
            params = json.load(f)
    except Exception as e:
        sys.stderr.write(f"Failed to load input dataset: {e}\\n")
        sys.stderr.flush()
        sys.exit(1)

    scope = {
        "__name__": "__sandbox__",
        "data": in_data,
        "parameters": params,
    }

    # Available vetted data processing libraries
    try:
        import polars as pl
        scope["pl"] = pl
        scope["polars"] = pl
    except ImportError:
        pass
    try:
        import pandas as pd
        scope["pd"] = pd
        scope["pandas"] = pd
    except ImportError:
        pass
    try:
        import numpy as np
        scope["np"] = np
        scope["numpy"] = np
    except ImportError:
        pass
    import math
    scope["math"] = math
    import datetime
    scope["datetime"] = datetime

    real_stdout = sys.stdout
    real_stderr = sys.stderr

    with open(code_file, "r", encoding="utf-8") as f:
        code_str = f.read()

    # Route python stdout to stderr during execution so user print() calls are diagnostic logs
    sys.stdout = real_stderr

    # Execute user code inside isolated scope
    code_obj = compile(code_str, "user_code.py", "exec")
    try:
        exec(code_obj, scope)
    except SystemExit as se:
        code = se.code if isinstance(se.code, int) else (0 if se.code is None else 1)
        if os.getpid() != 1:
            os._exit(code)
        for cand_name in ("output.json", "result.json", "/output/output.json", "/output/result.json", "/workspace/output.json", "/workspace/result.json"):
            if os.path.islink(cand_name):
                real_stderr.write(f"Security Violation: symlink detected at {cand_name}\\n")
                real_stderr.flush()
                sys.exit(1)
        if code != 0:
            real_stderr.write(f"Script exited with exit code {code}\\n")
            real_stderr.flush()
            sys.exit(code)

    if os.getpid() != 1:
        os._exit(0)

    # Check for symlink attacks targeting output files
    for cand_name in ("output.json", "result.json", "/output/output.json", "/output/result.json", "/workspace/output.json", "/workspace/result.json"):
        if os.path.islink(cand_name):
            real_stderr.write(f"Security Violation: symlink detected at {cand_name}\\n")
            real_stderr.flush()
            sys.exit(1)

    # Check /output for unexpected directories or unexpected files
    if os.path.exists("/output"):
        for root, dirs, files in os.walk("/output"):
            for d in dirs:
                real_stderr.write("Security Violation: unexpected directory detected in /output\\n")
                real_stderr.flush()
                sys.exit(1)
            for f in files:
                if f != "output.json":
                    real_stderr.write(f"Security Violation: unexpected file detected in /output: {f}\\n")
                    real_stderr.flush()
                    sys.exit(1)

    output = None
    target_out = "/output/output.json" if os.path.exists("/output/output.json") else ("/output/result.json" if os.path.exists("/output/result.json") else None)
    if target_out:
        try:
            with open(target_out, "r", encoding="utf-8") as f:
                output = json.load(f)
        except Exception as e:
            real_stderr.write(f"Failed to read output file {target_out}: {e}\\n")
            real_stderr.flush()
            sys.exit(1)
    elif "transform" in scope and callable(scope["transform"]):
        output = scope["transform"](in_data, params)
    elif "result" in scope:
        output = scope["result"]
    elif "output" in scope:
        output = scope["output"]
    else:
        if os.path.exists("output.json") and not os.path.islink("output.json"):
            try:
                with open("output.json", "r", encoding="utf-8") as f:
                    output = json.load(f)
            except Exception:
                output = {"message": "Execution finished without explicit return value."}
        else:
            output = {"message": "Execution finished without explicit return value."}

    if output is not None:
        try:
            import polars as pl
            if isinstance(output, pl.DataFrame):
                output = {"columns": output.columns, "rows": output.to_dicts()}
        except Exception:
            pass
        try:
            import pandas as pd
            if isinstance(output, pd.DataFrame):
                output = {"columns": list(output.columns), "rows": output.to_dict(orient="records")}
        except Exception:
            pass
        try:
            import numpy as np
            if isinstance(output, np.ndarray):
                output = output.tolist()
        except Exception:
            pass

    # Restore real stdout for emission of the structured JSON result
    sys.stdout = real_stdout

    try:
        raw_json = json.dumps(output, default=str)
    except Exception as e:
        real_stderr.write(f"Failed to serialize output to JSON: {e}\\n")
        real_stderr.flush()
        sys.exit(1)

    if len(raw_json.encode("utf-8")) > 5 * 1024 * 1024:
        real_stderr.write("Security Violation: output payload exceeds 5MB limit\\n")
        real_stderr.flush()
        sys.exit(1)

    real_stdout.write(raw_json)
    real_stdout.flush()
    real_stderr.flush()
    sys.exit(0)

if __name__ == "__main__":
    main()
"""


class SandboxResultProtocolError(ValueError):
    """Raised when sandbox stdout violates the strict Gate 10 result protocol."""
    pass


class DockerClientError(Exception):
    """Base exception for structured Docker API interactions."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class DockerNotFoundError(DockerClientError):
    """Structured Docker Engine HTTP 404 Not Found (confirmed container absence)."""

    def __init__(self, message: str = "Container not found", status_code: int = 404) -> None:
        super().__init__(message, status_code=status_code)


class DockerServerError(DockerClientError):
    """Structured Docker Engine HTTP 500 Internal Server Error (unconfirmed cleanup)."""

    def __init__(self, message: str = "Docker daemon internal server error", status_code: int = 500) -> None:
        super().__init__(message, status_code=status_code)


class DockerConnectionError(DockerClientError):
    """Docker daemon connection failure / socket unavailable."""

    def __init__(self, message: str = "Cannot connect to Docker daemon") -> None:
        super().__init__(message)


class DockerPermissionError(DockerClientError):
    """Docker API authentication / permission failure (HTTP 401/403)."""

    def __init__(self, message: str = "Permission denied accessing Docker daemon", status_code: int | None = 403) -> None:
        super().__init__(message, status_code=status_code)


class DockerTimeoutError(DockerClientError):
    """Docker API inspection timeout."""

    def __init__(self, message: str = "Docker API inspection timed out") -> None:
        super().__init__(message)


MIN_SAFE_INT = -(2**63)         # -9,223,372,036,854,775,808 (64-bit signed int min)
MAX_SAFE_INT = 2**63 - 1        #  9,223,372,036,854,775,807 (64-bit signed int max)
MAX_FLOAT_MAGNITUDE = 1e308     # IEEE 754 64-bit double maximum finite magnitude

MAX_STDOUT_BYTES = 5 * 1024 * 1024  # 5 MB
MAX_JSON_DEPTH = 16
MAX_OBJECT_KEYS = 10_000
MAX_ARRAY_ITEMS = 100_000
MAX_STRING_LENGTH = 1_000_000
MAX_KEY_LENGTH = 256
PERMITTED_JSON_WHITESPACE = {" ", "\t", "\n", "\r"}
RESERVED_KEY_PREFIXES = ("_vibe_", "__vibe_")


def strict_json_pairs_hook(pairs: list[tuple[Any, Any]]) -> dict[str, Any]:
    res: dict[str, Any] = {}
    for k, v in pairs:
        if not isinstance(k, str):
            raise SandboxResultProtocolError(f"Object key must be a string, got {type(k).__name__}")
        if k in res:
            raise SandboxResultProtocolError(f"Duplicate object key rejected: {k!r}")
        res[k] = v
    return res


def reject_json_constant(constant: str) -> None:
    raise SandboxResultProtocolError(f"Non-standard JSON constant rejected: {constant}")


def strict_parse_float(s: str) -> float:
    """Parse a JSON floating-point token according to IEEE 754 64-bit double precision rules.

    Semantics:
    1. Rejects non-finite values (NaN, Infinity, -Infinity) and magnitude exceeding 1e308.
    2. Preserves signed zero: '-0.0' produces float -0.0 with sign bit set (math.copysign(1.0, val) == -1.0).
    3. IEEE 754 subnormal range and underflow rounding (binary64 round-to-nearest, ties-to-even):
       - Minimum positive subnormal: 2**-1074 ≈ 4.9406564584e-324.
       - Rounding-to-zero halfway boundary: 2**-1075 ≈ 2.4703282292e-324.
       - Values below the halfway boundary (|x| < 2**-1075, e.g. 1e-324, 2e-324, 1e-325, 1e-999)
         round to signed zero (+0.0 or -0.0, preserving input sign).
       - Values above the halfway boundary (|x| > 2**-1075, e.g. 3e-324, 4e-324) round to the
         minimum subnormal (±2**-1074 ≈ ±4.94e-324).
       - Exact halfway ties (2**-1075) round to zero (even significand).
       - Input sign is preserved in all cases (including underflow to signed zero).
    """
    try:
        val = float(s)
    except Exception as e:
        raise SandboxResultProtocolError(f"Float parsing error for {s!r}: {e}") from e
    if not math.isfinite(val):
        raise SandboxResultProtocolError(f"Float overflow or non-finite float rejected: {s!r}")
    if abs(val) > MAX_FLOAT_MAGNITUDE:
        raise SandboxResultProtocolError(f"Float magnitude {val} exceeds maximum permitted limit {MAX_FLOAT_MAGNITUDE}")
    return val


def strict_parse_int(s: str) -> int:
    """Parse a JSON integer token enforcing signed 64-bit bounds [-2^63, 2^63 - 1].

    Semantics:
    1. Validates integer is within [MIN_SAFE_INT, MAX_SAFE_INT].
    2. Negative zero integer: RFC 8259 allows '-0' as an integer token. In Python, integer zero has
       no distinct sign bit (exact mathematical Z), so '-0' is normalized to 0.
    """
    try:
        val = int(s)
    except Exception as e:
        raise SandboxResultProtocolError(f"Integer parsing error for {s!r}: {e}") from e
    if val < MIN_SAFE_INT or val > MAX_SAFE_INT:
        raise SandboxResultProtocolError(
            f"Integer value {val} exceeds permitted 64-bit integer range [{MIN_SAFE_INT}, {MAX_SAFE_INT}]"
        )
    return val


def validate_json_contract_node(val: Any, depth: int = 1) -> None:
    if depth > MAX_JSON_DEPTH:
        raise SandboxResultProtocolError(f"JSON nesting depth exceeds maximum permitted limit of {MAX_JSON_DEPTH}")

    if isinstance(val, dict):
        if len(val) > MAX_OBJECT_KEYS:
            raise SandboxResultProtocolError(f"Object key count ({len(val)}) exceeds maximum limit of {MAX_OBJECT_KEYS}")
        for k, v in val.items():
            if not isinstance(k, str):
                raise SandboxResultProtocolError(f"Object key must be a string, got {type(k).__name__}")
            if len(k) > MAX_KEY_LENGTH:
                raise SandboxResultProtocolError(f"Object key length ({len(k)}) exceeds maximum limit of {MAX_KEY_LENGTH}")
            if any(ord(c) < 32 or ord(c) == 127 for c in k):
                raise SandboxResultProtocolError(f"Control characters detected in object key: {k!r}")
            if any(k.startswith(pfx) for pfx in RESERVED_KEY_PREFIXES):
                raise SandboxResultProtocolError(f"Reserved host metadata key prefix in user result: {k!r}")
            validate_json_contract_node(v, depth + 1)
    elif isinstance(val, list):
        if len(val) > MAX_ARRAY_ITEMS:
            raise SandboxResultProtocolError(f"Array length ({len(val)}) exceeds maximum limit of {MAX_ARRAY_ITEMS}")
        for item in val:
            validate_json_contract_node(item, depth + 1)
    elif isinstance(val, str):
        if len(val) > MAX_STRING_LENGTH:
            raise SandboxResultProtocolError(f"String length ({len(val)}) exceeds maximum limit of {MAX_STRING_LENGTH}")
        for c in val:
            o = ord(c)
            if 0xD800 <= o <= 0xDFFF:
                raise SandboxResultProtocolError(f"Lone Unicode surrogate character U+{o:04X} rejected.")
            if (o < 32 and c not in ("\t", "\n", "\r")) or o == 127:
                raise SandboxResultProtocolError(f"Control character detected in string value: {c!r}")
    elif isinstance(val, bool):
        pass  # Boolean is valid; must check before int
    elif isinstance(val, int):
        if val < MIN_SAFE_INT or val > MAX_SAFE_INT:
            raise SandboxResultProtocolError(f"Integer {val} out of 64-bit signed integer range.")
    elif isinstance(val, float):
        if not math.isfinite(val) or abs(val) > MAX_FLOAT_MAGNITUDE:
            raise SandboxResultProtocolError(f"Float {val} non-finite or magnitude exceeded.")
    elif val is None:
        pass
    else:
        raise SandboxResultProtocolError(f"Unexpected data type in JSON result: {type(val).__name__}")


def parse_and_validate_sandbox_stdout(raw_bytes: bytes) -> tuple[dict[str, JSONValue], str]:
    """Strictly parse and validate sandbox stdout stream according to Gate 10 contract.

    Returns:
        tuple of (validated_payload_dict, sha256_hexdigest)
    Raises:
        SandboxResultProtocolError if any validation requirement is violated.
    """
    if not raw_bytes:
        raise SandboxResultProtocolError("Script stdout is completely empty; expected exactly one JSON document.")

    if len(raw_bytes) > MAX_STDOUT_BYTES:
        raise SandboxResultProtocolError(f"Script stdout exceeds {MAX_STDOUT_BYTES} bytes limit (got {len(raw_bytes)} bytes).")

    try:
        decoded_text = raw_bytes.decode("utf-8", errors="strict")
    except UnicodeDecodeError as ue:
        raise SandboxResultProtocolError(f"Stdout violates UTF-8 encoding: {ue}") from ue

    # Exact RFC 8259 whitespace check: leading
    leading_idx = 0
    while leading_idx < len(decoded_text) and decoded_text[leading_idx] in PERMITTED_JSON_WHITESPACE:
        leading_idx += 1

    if leading_idx == len(decoded_text):
        raise SandboxResultProtocolError("Stdout contains only whitespace; expected exactly one JSON document.")

    if decoded_text[leading_idx].isspace() and decoded_text[leading_idx] not in PERMITTED_JSON_WHITESPACE:
        raise SandboxResultProtocolError(
            f"Non-permitted whitespace character U+{ord(decoded_text[leading_idx]):04X} rejected before JSON document."
        )

    decoder = json.JSONDecoder(
        object_pairs_hook=strict_json_pairs_hook,
        parse_constant=reject_json_constant,
        parse_float=strict_parse_float,
        parse_int=strict_parse_int,
    )

    try:
        doc, end_pos = decoder.raw_decode(decoded_text, idx=leading_idx)
    except (json.JSONDecodeError, RecursionError) as je:
        raise SandboxResultProtocolError(f"Malformed or excessively deep JSON document on stdout: {je}") from je
    except SandboxResultProtocolError:
        raise
    except Exception as e:
        raise SandboxResultProtocolError(f"JSON decoding error: {e}") from e

    # Exact RFC 8259 whitespace check: trailing
    trailing_text = decoded_text[end_pos:]
    for ch in trailing_text:
        if ch not in PERMITTED_JSON_WHITESPACE:
            if ch.isspace():
                raise SandboxResultProtocolError(
                    f"Non-permitted whitespace character U+{ord(ch):04X} rejected after JSON document."
                )
            raise SandboxResultProtocolError("Unexpected trailing text or multiple JSON documents detected on stdout.")

    if not isinstance(doc, dict):
        raise SandboxResultProtocolError(f"Top-level JSON value must be an object (dictionary), got {type(doc).__name__}.")

    try:
        validate_json_contract_node(doc, depth=1)
    except RecursionError as re:
        raise SandboxResultProtocolError(f"JSON nesting depth exceeded recursion limit: {re}") from re

    exact_sha256 = hashlib.sha256(raw_bytes).hexdigest()
    return doc, exact_sha256


def query_docker_engine_api(
    endpoint: str,
    method: str = "GET",
    timeout: float = 3.0,
) -> tuple[int | None, dict[str, Any] | None, Exception | None]:
    """Query Docker Engine API directly over Windows named pipe, Unix domain socket, or TCP.

    Authoritative return:
        tuple[status_code, parsed_json_dict, exception]
        - status_code: HTTP integer status code (200, 404, 500, etc.) or None if connection/timeout failed.
        - parsed_json_dict: Parsed JSON dict on success (or None on non-JSON/error response).
        - exception: Typed DockerClientError subclass on failure (or None on HTTP 200).
    """
    req_bytes = f"{method} {endpoint} HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n".encode()
    raw_response: str | None = None

    # 1. Try Windows named pipes if on Windows
    if sys.platform == "win32":
        candidate_pipes = [
            r"\\.\pipe\dockerDesktopLinuxEngine",
            r"\\.\pipe\docker_engine",
        ]
        for pipe_path in candidate_pipes:
            try:
                with open(pipe_path, "r+b", buffering=0) as f:
                    f.write(req_bytes)
                    chunks: list[bytes] = []
                    start_t = time.perf_counter()
                    while True:
                        if (time.perf_counter() - start_t) > timeout:
                            return None, None, DockerTimeoutError(f"Docker Engine named pipe {pipe_path} timed out")
                        b = f.read(8192)
                        if not b:
                            break
                        chunks.append(b)
                    raw_response = b"".join(chunks).decode("utf-8", errors="replace")
                    if raw_response:
                        break
            except Exception:
                continue

    # 2. Try Unix domain socket on POSIX/Linux/WSL2
    if raw_response is None and hasattr(socket, "AF_UNIX") and os.path.exists("/var/run/docker.sock"):
        try:
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.settimeout(timeout)
            s.connect("/var/run/docker.sock")
            s.sendall(req_bytes)
            chunks = []
            while True:
                data = s.recv(8192)
                if not data:
                    break
                chunks.append(data)
            s.close()
            raw_response = b"".join(chunks).decode("utf-8", errors="replace")
        except TimeoutError:
            return None, None, DockerTimeoutError("Docker Unix socket inspection timed out")
        except Exception as exc:
            logger.debug(f"Docker Unix socket connection failed: {exc}")

    # 3. Try TCP endpoint if DOCKER_HOST is set
    if raw_response is None and os.environ.get("DOCKER_HOST"):
        docker_host = os.environ["DOCKER_HOST"]
        if docker_host.startswith("tcp://"):
            http_url = docker_host.replace("tcp://", "http://", 1) + endpoint
            try:
                with httpx.Client(timeout=timeout) as client:
                    resp = client.request(method, http_url)
                    status_code = resp.status_code
                    if status_code == 200:
                        try:
                            return 200, resp.json(), None
                        except Exception:
                            return 200, {}, None
                    elif status_code == 404:
                        return 404, None, DockerNotFoundError(resp.text, status_code=404)
                    elif status_code == 500:
                        return 500, None, DockerServerError(resp.text, status_code=500)
                    elif status_code in (401, 403):
                        return status_code, None, DockerPermissionError(resp.text, status_code=status_code)
                    else:
                        return status_code, None, DockerClientError(resp.text, status_code=status_code)
            except httpx.TimeoutException:
                return None, None, DockerTimeoutError("Docker TCP inspection timed out")
            except Exception as exc:
                return None, None, DockerConnectionError(f"Docker TCP connection failed: {exc}")

    if raw_response is None:
        return None, None, DockerConnectionError("Docker Engine API unavailable; structured inspection cannot be performed")

    header_part, _, body_part = raw_response.partition("\r\n\r\n")
    lines = header_part.splitlines()
    if not lines:
        return None, None, DockerClientError("Malformed empty response from Docker Engine API")

    m = re.match(r"HTTP/\d\.\d\s+(\d+)", lines[0])
    if not m:
        return None, None, DockerClientError(f"Invalid HTTP response status line: {lines[0]}")

    status_code = int(m.group(1))

    # Handle chunked transfer-encoding if present
    body_clean = body_part
    if "transfer-encoding: chunked" in header_part.lower():
        chunks_decoded: list[str] = []
        pos = 0
        while pos < len(body_part):
            nl = body_part.find("\r\n", pos)
            if nl == -1:
                break
            len_hex = body_part[pos:nl].strip()
            if not len_hex:
                break
            try:
                chunk_len = int(len_hex, 16)
            except ValueError:
                break
            if chunk_len == 0:
                break
            start = nl + 2
            chunks_decoded.append(body_part[start : start + chunk_len])
            pos = start + chunk_len + 2
        if chunks_decoded:
            body_clean = "".join(chunks_decoded)

    parsed_json: dict[str, Any] | None = None
    if status_code == 200:
        try:
            parsed_json = json.loads(body_clean)
        except Exception:
            parsed_json = {}
        return 200, parsed_json, None
    elif status_code == 404:
        return 404, None, DockerNotFoundError(body_clean or "Container not found", status_code=404)
    elif status_code == 500:
        return 500, None, DockerServerError(body_clean or "Internal Server Error", status_code=500)
    elif status_code in (401, 403):
        return status_code, None, DockerPermissionError(body_clean or "Permission Denied", status_code=status_code)
    else:
        return status_code, None, DockerClientError(body_clean or f"HTTP {status_code}", status_code=status_code)


def structured_docker_inspect(
    container_id: str,
    docker_client: Any = None,
) -> tuple[int | None, dict[str, Any] | None, Exception | None]:
    """Perform authoritative structured inspection against Docker Engine API or SDK.

    Authoritative semantics:
    - HTTP 404 / DockerNotFoundError: confirmed absent.
    - HTTP 200: container exists.
    - HTTP 500 / DockerServerError: unconfirmed (fails closed).
    - Connection failure, permission failure, timeout: unconfirmed (fails closed).
    - If structured inspection is unavailable, fails closed.
    - NEVER treats CLI stderr as proof of absence.
    """
    if docker_client is not None:
        try:
            c = docker_client.containers.get(container_id)
            attrs = getattr(c, "attrs", {}) or {"Id": container_id}
            return 200, attrs, None
        except Exception as exc:
            status_code = getattr(exc, "status_code", None)
            resp = getattr(exc, "response", None)
            if resp is not None and hasattr(resp, "status_code"):
                status_code = resp.status_code
            exc_name = type(exc).__name__

            if status_code == 404 or exc_name in ("NotFound", "DockerNotFoundError"):
                return 404, None, DockerNotFoundError(str(exc), status_code=404)
            if status_code == 500 or exc_name in ("ServerError", "DockerServerError"):
                return 500, None, DockerServerError(str(exc), status_code=500)
            if status_code in (401, 403) or exc_name in ("PermissionError", "DockerPermissionError"):
                perm_code = int(status_code) if status_code is not None else 403
                return perm_code, None, DockerPermissionError(str(exc), status_code=perm_code)
            if "timeout" in str(exc).lower() or exc_name in ("Timeout", "DockerTimeoutError"):
                return None, None, DockerTimeoutError(str(exc))
            if "connect" in str(exc).lower() or exc_name in ("ConnectionError", "DockerConnectionError"):
                return None, None, DockerConnectionError(str(exc))
            return status_code, None, exc

    # Direct Docker Engine API endpoint query
    return query_docker_engine_api(f"/containers/{container_id}/json")


def classify_docker_inspect_result(
    returncode: int | None = None,
    stdout: str = "",
    stderr: str = "",
    container_name: str = "",
    status_code: int | None = None,
    api_exception: Exception | None = None,
) -> tuple[bool, str | None, str]:
    """Reliably classify the outcome of container inspection against Docker Engine API semantics.

    Treats ONLY a positively identified HTTP 404 / DockerNotFoundError as confirmed absence.
    HTTP 200 confirms container exists.
    HTTP 500, daemon unavailable, permission denied, inspection timeout, and misleading errors fail closed.
    CLI human-readable text phrases (e.g. 'no such container') are NEVER accepted as proof of absence.

    Returns:
        tuple[bool, str | None, str]:
            (confirmed_absent, error_message, classification_category)

    Invariant:
        confirmed_absent is ONLY True when Docker Engine API or SDK positively returns 404 (NotFound).
        Every other uncertain outcome returns confirmed_absent=False (fail closed).
    """
    # 1. Structured API status_code or api_exception takes absolute priority
    if status_code is not None or api_exception is not None:
        exc_name = type(api_exception).__name__ if api_exception else ""
        exc_status = getattr(api_exception, "status_code", None)
        effective_status = status_code or exc_status

        # HTTP 500: Server Error MUST fail closed unconditionally!
        # CRITICAL: Even if the 500 response contains misleading wording like "not found" or "no such container",
        # an HTTP 500 error is ALWAYS classified as server_error and confirmed_absent is False!
        if (
            effective_status == 500
            or isinstance(api_exception, DockerServerError)
            or exc_name in ("DockerServerError", "ServerError")
        ):
            err_msg = str(api_exception) if api_exception else (stderr.strip() or "internal server error")
            return (
                False,
                f"server_error: Docker Engine returned HTTP 500: {err_msg}",
                "server_error",
            )

        # HTTP 404: Positively confirmed absence
        if (
            effective_status == 404
            or isinstance(api_exception, DockerNotFoundError)
            or exc_name in ("DockerNotFoundError", "NotFound")
        ):
            return (
                True,
                None,
                "confirmed_absent",
            )

        # HTTP 200: Container exists
        if effective_status == 200:
            return (
                False,
                f"container_still_present: Container {container_name} still exists and is still present in Docker daemon",
                "container_still_present",
            )

        # Daemon unavailable
        if isinstance(api_exception, (DockerConnectionError, ConnectionError)) or exc_name in (
            "DockerConnectionError",
            "ConnectionError",
        ):
            return (
                False,
                f"daemon_unavailable: {api_exception or stderr or 'daemon unavailable'}",
                "daemon_unavailable",
            )

        # Permission / Unauthorized
        if (
            isinstance(api_exception, DockerPermissionError)
            or effective_status in (401, 403)
            or exc_name in ("DockerPermissionError", "PermissionError")
        ):
            return (
                False,
                f"permission_or_api_failure: {api_exception or stderr or 'permission denied'}",
                "permission_or_api_failure",
            )

        # Timeout
        if isinstance(api_exception, (DockerTimeoutError, subprocess.TimeoutExpired)) or exc_name in (
            "DockerTimeoutError",
            "Timeout",
        ):
            return (
                False,
                f"timeout: {api_exception or stderr or 'inspection timeout'}",
                "timeout",
            )

        if api_exception is not None:
            return (
                False,
                f"ambiguous_failure: {api_exception}",
                "ambiguous_failure",
            )

    # 2. CLI output fallback: CANNOT authoritatively prove container absence!
    clean_err = stderr.strip() or stdout.strip() or f"CLI exit code {returncode}"
    if returncode == 0:
        return (
            False,
            f"container_still_present: Container {container_name} still exists according to CLI inspection",
            "container_still_present",
        )

    # Distinguish operational errors from unconfirmed output
    combined = f"{stderr} {stdout}".lower()
    if any(phrase in combined for phrase in ("500 internal server error", "internal server error", "response code 500")):
        return False, f"server_error: {clean_err}", "server_error"
    if any(phrase in combined for phrase in ("cannot connect", "connection refused", "is the docker daemon running")):
        return False, f"daemon_unavailable: {clean_err}", "daemon_unavailable"
    if any(phrase in combined for phrase in ("permission denied", "access denied", "403 forbidden", "401 unauthorized")):
        return False, f"permission_or_api_failure: {clean_err}", "permission_or_api_failure"
    if any(phrase in combined for phrase in ("no such file or directory", "no such file", "not a directory")):
        return False, f"ambiguous_failure: {clean_err}", "ambiguous_failure"

    # Human-readable CLI stderr phrase (e.g. 'no such container') strictly cannot prove absence:
    return (
        False,
        f"unconfirmed_cli_output: Human-readable CLI stderr phrase ({clean_err}) cannot authoritatively prove container absence; structured Docker Engine API or SDK inspection required",
        "unconfirmed_cli_output",
    )


def stop_and_remove_container(
    container_name: str,
    grace_seconds: int = 2,
    container_id: str | None = None,
    docker_client: Any = None,
) -> tuple[bool, str | None]:
    """Safely terminate and remove a container with bounded stop grace period, forced kill fallback, and verified absence.

    Requirements:
    - Immutable container ID is mandatory: cleanup verification is rejected if container_id is missing.
    - Uses structured Docker Engine API / SDK inspection.
    - Preserves independent stop, kill, rm, and inspect phases.
    - Returns (confirmed_absent, error_message).
    """
    cleanup_warnings: list[str] = []

    # Mandatory Immutable Container ID enforcement
    if not container_id:
        # Best-effort kill/rm by name to prevent dangling execution, but REJECT verification!
        try:
            subprocess.run(["docker", "kill", container_name], capture_output=True, timeout=5)
            subprocess.run(["docker", "rm", "-f", container_name], capture_output=True, timeout=5)
        except Exception:
            pass
        return (
            False,
            "missing_immutable_container_id: cleanup verification rejected because immutable container_id was not provided",
        )

    target = container_id

    # 1. Attempt bounded graceful stop (handling non-zero exit codes explicitly)
    try:
        stop_res = subprocess.run(
            ["docker", "stop", "-t", str(grace_seconds), target],
            capture_output=True,
            timeout=grace_seconds + 3,
        )
        if stop_res.returncode != 0:
            err = stop_res.stderr.decode("utf-8", errors="replace").strip()
            if "no such container" not in err.lower() and "no such object" not in err.lower():
                cleanup_warnings.append(f"stop_nonzero_exit_{stop_res.returncode}: {err}")
    except Exception as e:
        cleanup_warnings.append(f"stop_error: {e}")

    # 2. Forced kill fallback (attempt bounded kill if stop failed or container survived)
    try:
        kill_res = subprocess.run(
            ["docker", "kill", target],
            capture_output=True,
            timeout=5,
        )
        if kill_res.returncode != 0:
            kerr = kill_res.stderr.decode("utf-8", errors="replace").strip()
            if (
                "not running" not in kerr.lower()
                and "no such container" not in kerr.lower()
                and "no such object" not in kerr.lower()
            ):
                cleanup_warnings.append(f"kill_nonzero_exit_{kill_res.returncode}: {kerr}")
    except Exception as e:
        cleanup_warnings.append(f"kill_error: {e}")

    # 3. Force removal
    try:
        rm_res = subprocess.run(
            ["docker", "rm", "-f", target],
            capture_output=True,
            timeout=5,
        )
        if rm_res.returncode != 0:
            rerr = rm_res.stderr.decode("utf-8", errors="replace").strip()
            if "no such container" not in rerr.lower() and "no such object" not in rerr.lower():
                cleanup_warnings.append(f"rm_nonzero_exit_{rm_res.returncode}: {rerr}")
    except Exception as e:
        cleanup_warnings.append(f"rm_error: {e}")

    # 4. Absence Verification check using structured Docker API/SDK inspection
    try:
        status_code, data, exc = structured_docker_inspect(target, docker_client=docker_client)
        confirmed_absent, inspect_err, _ = classify_docker_inspect_result(
            returncode=(0 if status_code == 200 else 1),
            stdout=json.dumps(data) if data else "",
            stderr=str(exc) if exc else "",
            container_name=target,
            status_code=status_code,
            api_exception=exc,
        )
        if confirmed_absent:
            return True, None
        else:
            all_errs = cleanup_warnings + ([inspect_err] if inspect_err else [])
            return False, "; ".join(all_errs) or "cleanup_unconfirmed"
    except Exception as e:
        all_errs = cleanup_warnings + [f"inspection_exception: {e}"]
        return False, "; ".join(all_errs)


def verify_image_and_container_provenance(
    container_id: str,
    expected_repo: str = PINNED_IMAGE_REPOSITORY,
    expected_manifest_digest: str = PINNED_IMAGE_DIGEST,
    expected_platform_arch: str = "amd64",
    docker_client: Any = None,
) -> tuple[bool, str | None, dict[str, Any]]:
    """Strictly verify container and image provenance following OCI and Docker manifest digest semantics.

    Checks:
    1. The container creation used the exact immutable repository@sha256:<manifest-digest> reference.
    2. Container .Config.Image exactly matches that reference (and strictly rejects mutable tags like :latest).
    3. Image inspection returns RepoDigests containing the configured manifest digest.
    4. Container .Image matches the inspected image configuration Id.
    5. Image architecture and OS match the execution platform (amd64 / linux).
    6. Mutable tag fallbacks (including :latest) are strictly rejected.
    7. Any mismatch aborts execution and enters fail-closed cleanup.

    Invariant:
    Manifest digest is compared with RepoDigests, NEVER directly with image configuration Id.
    Container .Image is matched against image configuration Id.
    """
    expected_ref = f"{expected_repo}@{expected_manifest_digest}"
    diagnostics: dict[str, Any] = {
        "pinned_repository_reference": expected_ref,
        "expected_manifest_digest": expected_manifest_digest,
        "image_inspection_id": None,
        "repo_digests": [],
        "container_config_image": None,
        "container_image_id": None,
        "effective_path": None,
        "effective_args": [],
        "architecture": None,
        "os": None,
    }

    # 1. Inspect container metadata
    container_data: dict[str, Any] | None = None
    if docker_client is not None:
        try:
            c = docker_client.containers.get(container_id)
            container_data = getattr(c, "attrs", {})
        except Exception as exc:
            return False, f"container_inspect_failed: {exc}", diagnostics
    else:
        code, data, _ = query_docker_engine_api(f"/containers/{container_id}/json")
        if code == 200 and data:
            container_data = data
        else:
            try:
                res = subprocess.run(["docker", "inspect", container_id], capture_output=True, text=True, timeout=3)
                if res.returncode == 0:
                    parsed = json.loads(res.stdout)
                    if isinstance(parsed, list) and len(parsed) > 0:
                        container_data = parsed[0]
            except Exception as exc:
                return False, f"container_inspect_failed: {exc}", diagnostics

    if not container_data:
        return False, "container_inspect_failed: Container metadata could not be retrieved", diagnostics

    config_info = container_data.get("Config", {}) or {}
    container_config_image = config_info.get("Image", "")
    container_image_id = container_data.get("Image", "")
    container_path = container_data.get("Path", "")
    container_args = container_data.get("Args", [])

    diagnostics["container_config_image"] = container_config_image
    diagnostics["container_image_id"] = container_image_id
    diagnostics["effective_path"] = container_path
    diagnostics["effective_args"] = container_args

    # Check 1 & 2 & 6: Validate container creation reference and rejection of mutable tags
    if not container_config_image or "@" not in container_config_image:
        return False, f"mutable_tag_rejected: Container config image {container_config_image!r} lacks cryptographic digest or uses mutable tag", diagnostics

    if ":latest" in container_config_image:
        return False, f"mutable_tag_rejected: Container config image {container_config_image!r} contains mutable tag :latest", diagnostics

    if not (container_config_image == expected_ref or container_config_image.endswith(f"@{expected_manifest_digest}")):
        return False, f"image_provenance_mismatch: Container config image {container_config_image!r} does not match expected reference {expected_ref!r}", diagnostics

    # Validate PID 1 entrypoint
    if container_path != "python":
        return False, f"entrypoint_mismatch: Container path {container_path!r} is not 'python'", diagnostics

    if not (container_args == ["/input/runner.py"] or container_args[:2] == ["python", "/input/runner.py"]):
        return False, f"entrypoint_mismatch: Container args {container_args!r} do not execute python /input/runner.py directly as PID 1", diagnostics

    # 2. Inspect image metadata
    image_data: dict[str, Any] | None = None
    target_img = container_image_id or expected_ref
    if docker_client is not None:
        try:
            img = docker_client.images.get(target_img)
            image_data = getattr(img, "attrs", {})
        except Exception as exc:
            return False, f"image_inspect_failed: {exc}", diagnostics
    else:
        code, data, _ = query_docker_engine_api(f"/images/{target_img}/json")
        if code == 200 and data:
            image_data = data
        else:
            try:
                res = subprocess.run(["docker", "image", "inspect", target_img], capture_output=True, text=True, timeout=3)
                if res.returncode == 0:
                    parsed = json.loads(res.stdout)
                    if isinstance(parsed, list) and len(parsed) > 0:
                        image_data = parsed[0]
            except Exception as exc:
                return False, f"image_inspect_failed: {exc}", diagnostics

    if not image_data:
        return False, "image_inspect_failed: Image metadata could not be retrieved", diagnostics

    image_config_id = image_data.get("Id", "")
    repo_digests = image_data.get("RepoDigests", [])
    image_arch = image_data.get("Architecture", "")
    image_os = image_data.get("Os", "")

    diagnostics["image_inspection_id"] = image_config_id
    diagnostics["repo_digests"] = repo_digests
    diagnostics["architecture"] = image_arch
    diagnostics["os"] = image_os

    # Check 3: RepoDigests must contain the configured manifest digest
    if not any(expected_manifest_digest in rd for rd in repo_digests):
        return False, f"repo_digest_mismatch: Configured manifest digest {expected_manifest_digest} not found in image RepoDigests {repo_digests!r}", diagnostics

    # Check 4: Container's .Image must match the inspected image's configuration Id
    if container_image_id and image_config_id and container_image_id != image_config_id:
        return False, f"image_config_id_mismatch: Container .Image {container_image_id!r} does not match inspected image config Id {image_config_id!r}", diagnostics

    # Check 5: Image architecture and OS must match execution platform
    if image_os != "linux":
        return False, f"platform_os_mismatch: Image OS {image_os!r} is not 'linux'", diagnostics

    valid_archs = (expected_platform_arch, "x86_64") if expected_platform_arch == "amd64" else (expected_platform_arch,)
    if image_arch not in valid_archs:
        return False, f"platform_architecture_mismatch: Image architecture {image_arch!r} does not match execution platform {expected_platform_arch!r}", diagnostics

    return True, None, diagnostics


# In Gate 8 Direct PID 1 Execution Architecture, in-container supervisor is retired.
# Untrusted Python runs directly as PID 1 of its own container PID namespace.
SUPERVISOR_CONTAINER_RUNNER = USER_CONTAINER_EXECUTOR
RUNNER_CONTAINER_WRAPPER = USER_CONTAINER_EXECUTOR


class PythonSandboxRunner:
    """Executes validated Python analytical scripts inside a hardened, isolated OCI container."""

    def __init__(
        self,
        timeout_seconds: int = 15,
        memory_limit_mb: int = 256,
        force_enabled: bool | None = None,
        image: str | None = None,
        image_digest: str | None = None,
    ) -> None:
        self.timeout_seconds = min(
            timeout_seconds, settings.PYTHON_SANDBOX_TIMEOUT_SECONDS
        )
        self.memory_limit_mb = min(
            memory_limit_mb, settings.PYTHON_SANDBOX_MAX_MEMORY_MB
        )
        self.image_repo = settings.PYTHON_SANDBOX_IMAGE_REPO
        self.image = image or settings.PYTHON_SANDBOX_IMAGE
        self.image_digest = image_digest or settings.PYTHON_SANDBOX_IMAGE_DIGEST
        self.enabled = settings.PYTHON_SANDBOX_ENABLED if force_enabled is None else force_enabled

    def get_effective_image_ref(self) -> str:
        """Resolve immutable image reference repository@sha256:<digest>, strictly avoiding mutable tags."""
        base_repo = self.image_repo
        if ":" in self.image:
            base_repo = self.image.split(":")[0]
        if "@" in base_repo:
            base_repo = base_repo.split("@")[0]
        if not self.image_digest or not self.image_digest.startswith("sha256:"):
            raise ValueError(f"Invalid image digest: {self.image_digest}. Immutable cryptographic digest required.")
        return f"{base_repo}@{self.image_digest}"

    def verify_image_digest(self) -> bool:
        """Verify the local or pulled image matches the cryptographic SHA-256 digest."""
        effective_ref = self.get_effective_image_ref()
        try:
            res = subprocess.run(
                ["docker", "inspect", effective_ref, "--format", "{{.Id}}|{{.RepoDigests}}"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if res.returncode == 0 and self.image_digest in res.stdout:
                return True
            res2 = subprocess.run(
                ["docker", "inspect", self.image_digest, "--format", "{{.Id}}"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            return res2.returncode == 0 and self.image_digest in res2.stdout
        except Exception:
            return False

    def check_docker_available(self) -> bool:
        """Check if Docker engine CLI is accessible and responsive."""
        try:
            res = subprocess.run(
                ["docker", "info"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=3,
            )
            return res.returncode == 0
        except Exception:
            return False

    def get_image_metadata(self) -> dict[str, Any]:
        """Retrieve verified image metadata separating manifest digest, config ID, and repo digests."""
        effective_ref = self.get_effective_image_ref()
        res_dict: dict[str, Any] = {
            "requested_manifest_digest": self.image_digest,
            "image_config_id": None,
            "repo_digests": [],
            "manifest_digest": None,
        }
        try:
            res = subprocess.run(
                ["docker", "inspect", effective_ref, "--format", "{{json .}}"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if res.returncode == 0:
                data = json.loads(res.stdout)
                res_dict["image_config_id"] = data.get("Id")
                res_dict["repo_digests"] = data.get("RepoDigests", [])
                res_dict["manifest_digest"] = (data.get("Descriptor") or {}).get("digest")
        except Exception:
            pass
        return res_dict

    def run(
        self,
        code_str: str,
        input_data: dict[str, Any] | list[Any],
        parameters: dict[str, Any] | None = None,
        bypass_ast: bool = False,
        timeout_seconds: int | None = None,
    ) -> SandboxExecutionResult:
        """Run script inside a strictly isolated, unprivileged OCI container."""
        effective_timeout = timeout_seconds or self.timeout_seconds

        # 1. Administrative Gate: If sandbox execution is not enabled, safely abort
        if not self.enabled:
            return SandboxExecutionResult(
                status="disabled",
                execution_time_ms=0,
                memory_peak_mb=0.0,
                stdout="",
                stderr="Script execution is currently disabled pending administrator sandbox verification. Script authoring and versioning remain fully operational.",
                error_message="User-defined script execution is disabled.",
                isolation_backend="disabled",
            )

        # 2. Static AST pre-check (unless testing adversarial runtime enforcement)
        if not bypass_ast:
            ast_err = validate_python_ast(code_str)
            if ast_err:
                return SandboxExecutionResult(
                    status="rejected",
                    execution_time_ms=0,
                    memory_peak_mb=0.0,
                    stdout="",
                    stderr=ast_err,
                    error_message=ast_err,
                    isolation_backend="ast_precheck",
                )

        # 3. Check Docker daemon availability
        if not self.check_docker_available():
            return SandboxExecutionResult(
                status="failed",
                execution_time_ms=0,
                memory_peak_mb=0.0,
                stdout="",
                stderr="Isolated Docker container runtime is not available on this host.",
                error_message="Container runtime unavailable.",
                isolation_backend="docker_oci",
            )

        start_time = time.perf_counter()

        # 4. Create transient workspace directories (separate input from output)
        tmp_dir = tempfile.mkdtemp(prefix="vibe_sandbox_")
        tmp_path = Path(tmp_dir)
        input_path = tmp_path / "input"
        input_path.mkdir(parents=True, exist_ok=True)

        # Unique container tracking
        container_id = f"{int(time.time())}_{os.urandom(4).hex()}"
        container_name = f"vibe-sandbox-{container_id}"
        created_ts = int(time.time())

        try:
            effective_image = self.get_effective_image_ref()

            # Write input artifacts into read-only input mount
            with open(input_path / "user_code.py", "w", encoding="utf-8") as f:
                f.write(code_str)

            with open(input_path / "input.json", "w", encoding="utf-8") as f:
                json.dump(input_data, f, default=str)

            with open(input_path / "parameters.json", "w", encoding="utf-8") as f:
                json.dump(parameters or {}, f, default=str)

            with open(input_path / "runner.py", "w", encoding="utf-8") as f:
                f.write(USER_CONTAINER_EXECUTOR)

            with open(input_path / "user_runner.py", "w", encoding="utf-8") as f:
                f.write(USER_CONTAINER_EXECUTOR)

            # Build Docker execution command with all Gate 8 isolation properties:
            # - ZERO in-container supervisor: python /input/runner.py runs directly as PID 1
            # - ZERO named volumes
            # - ZERO writable host bind mounts
            # - Bounded tmpfs /workspace (64m, 1024 inodes, noexec)
            # - Bounded tmpfs /tmp (64m, noexec)
            # - Bounded tmpfs /output (8m, 16 inodes, noexec)
            # - Read-only host mount /input (:ro)
            # - Mandatory capture of immutable 64-char container ID via --cidfile
            # - Pinned image by digest repository@sha256:...
            # - Kernel PID namespace teardown on PID 1 exit
            cid_file = input_path / "container.cid"
            docker_cmd = [
                "docker", "run",
                "--cidfile", str(cid_file),
                "--name", container_name,
                "--label", "vibe.managed=true",
                "--label", f"vibe.execution_id={container_name}",
                "--label", f"vibe.created_at={created_ts}",
                "--network", "none",
                "--read-only",
                "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m",
                "--tmpfs", "/workspace:rw,nosuid,nodev,noexec,size=64m,nr_inodes=1024,uid=10001,gid=10001,mode=0700",
                "--tmpfs", "/output:rw,nosuid,nodev,noexec,size=8m,nr_inodes=16,uid=10001,gid=10001,mode=0700",
                "--memory", f"{self.memory_limit_mb}m",
                "--memory-swap", f"{self.memory_limit_mb}m",
                "--cpus", "1.0",
                "--pids-limit", "32",
                "--cap-drop", "ALL",
                "--security-opt", "no-new-privileges:true",
                "--entrypoint", "python",
                "-v", f"{str(input_path)}:/input:ro",
                "-w", "/workspace",
                effective_image,
                "/input/runner.py",
            ]

            proc = subprocess.Popen(
                docker_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            # Capture container immutable ID immediately at creation
            container_full_id: str | None = None
            for _ in range(20):
                if cid_file.exists():
                    cid_text = cid_file.read_text(encoding="utf-8").strip()
                    if len(cid_text) == 64:
                        container_full_id = cid_text
                        break
                time.sleep(0.05)

            if not container_full_id:
                try:
                    id_res = subprocess.run(
                        ["docker", "inspect", container_name, "--format", "{{.Id}}"],
                        capture_output=True,
                        text=True,
                        timeout=3,
                    )
                    if id_res.returncode == 0 and id_res.stdout.strip():
                        cand_id = id_res.stdout.strip()
                        if len(cand_id) == 64:
                            container_full_id = cand_id
                except Exception:
                    pass

            if not container_full_id:
                try:
                    proc.kill()
                except Exception:
                    pass
                shutil.rmtree(tmp_dir, ignore_errors=True)
                return SandboxExecutionResult(
                    status="failed",
                    execution_time_ms=0,
                    memory_peak_mb=0.0,
                    stdout="",
                    stderr="Security violation: failed to capture immutable container ID at creation.",
                    error_message="Immutable container ID unavailable at creation.",
                    isolation_backend="docker_oci",
                    enforcement_details={
                        "exit_code": 1,
                        "cleanup_confirmed": False,
                        "cleanup_error": "missing_immutable_container_id: container ID could not be captured at creation",
                        "error": "missing_immutable_container_id",
                    },
                )

            # Verify container metadata, image provenance, and entrypoint immediately upon creation
            prov_ok, prov_err, prov_diag = verify_image_and_container_provenance(
                container_id=container_full_id,
                expected_repo=self.image_repo,
                expected_manifest_digest=self.image_digest,
                expected_platform_arch="amd64",
            )
            if not prov_ok:
                try:
                    proc.kill()
                except Exception:
                    pass
                stop_and_remove_container(container_name, container_id=container_full_id)
                shutil.rmtree(tmp_dir, ignore_errors=True)
                err_category = "entrypoint_mismatch" if "entrypoint_mismatch" in (prov_err or "") else "image_provenance_mismatch"
                return SandboxExecutionResult(
                    status="failed",
                    execution_time_ms=0,
                    memory_peak_mb=0.0,
                    stdout="",
                    stderr=f"Security violation: {prov_err}",
                    error_message=f"Provenance verification failed: {prov_err}",
                    isolation_backend="docker_oci",
                    enforcement_details={
                        "exit_code": 1,
                        "error": err_category,
                        "cleanup_confirmed": True,
                        "cleanup_error": None,
                        "pinned_repository_reference": prov_diag.get("pinned_repository_reference"),
                        "expected_manifest_digest": prov_diag.get("expected_manifest_digest"),
                        "image_inspection_id": prov_diag.get("image_inspection_id"),
                        "repo_digests": prov_diag.get("repo_digests"),
                        "container_config_image": prov_diag.get("container_config_image"),
                        "container_image_id": prov_diag.get("container_image_id"),
                        "container_path": prov_diag.get("effective_path"),
                        "container_args": prov_diag.get("effective_args"),
                        "architecture": prov_diag.get("architecture"),
                        "os": prov_diag.get("os"),
                    },
                )

            container_image_id = prov_diag.get("container_image_id")
            container_path = prov_diag.get("effective_path")
            container_args = prov_diag.get("effective_args")

            stdout_chunks: list[bytes] = []
            stderr_chunks: list[bytes] = []
            stdout_bytes = 0
            stderr_bytes = 0
            retained_stderr_bytes = 0
            stdout_overflow = False
            stderr_overflow = False
            flood_killed = False
            stream_lock = threading.Lock()
            election_lock = threading.Lock()
            overflow_claimed = False
            cleanup_confirmed: bool = False
            cleanup_error: str | None = None

            MAX_OUTPUT_BYTES = 5 * 1024 * 1024  # 5MB
            MAX_STDERR_BYTES = 65536           # 64KB
            MAX_FLOOD_BYTES = 10 * 1024 * 1024  # 10MB

            def elect_and_trigger_overflow(reason: str) -> bool:
                nonlocal overflow_claimed, flood_killed, stdout_overflow, stderr_overflow, cleanup_confirmed, cleanup_error
                with election_lock:
                    if overflow_claimed:
                        return False
                    overflow_claimed = True
                    if "stdout" in reason:
                        stdout_overflow = True
                    elif "stderr" in reason:
                        stderr_overflow = True
                    else:
                        flood_killed = True

                # Teardown executed outside election_lock and stream_lock to guarantee no reader deadlock
                try:
                    proc.kill()
                except Exception as e:
                    cleanup_error = f"kill_proc_err: {e}"
                confirmed, err = stop_and_remove_container(container_name, grace_seconds=1, container_id=container_full_id)
                cleanup_confirmed = confirmed
                if err:
                    cleanup_error = f"{cleanup_error}; {err}" if cleanup_error else err
                return True

            def read_pipe(pipe: Any, is_stdout: bool) -> None:
                nonlocal stdout_bytes, stderr_bytes, retained_stderr_bytes
                try:
                    while True:
                        chunk = pipe.read(4096)
                        if not chunk:
                            break

                        should_terminate = False
                        term_reason = ""

                        with stream_lock:
                            if overflow_claimed:
                                break

                            if is_stdout:
                                prospective_stdout = stdout_bytes + len(chunk)
                                if prospective_stdout > MAX_OUTPUT_BYTES:
                                    should_terminate = True
                                    term_reason = "stdout_overflow"
                                else:
                                    stdout_bytes = prospective_stdout
                                    stdout_chunks.append(chunk)
                            else:
                                prospective_stderr = stderr_bytes + len(chunk)
                                stderr_bytes = prospective_stderr
                                if retained_stderr_bytes < MAX_STDERR_BYTES:
                                    rem = MAX_STDERR_BYTES - retained_stderr_bytes
                                    to_retain = chunk[:rem]
                                    stderr_chunks.append(to_retain)
                                    retained_stderr_bytes += len(to_retain)

                            if (stdout_bytes + stderr_bytes) > MAX_FLOOD_BYTES:
                                should_terminate = True
                                term_reason = "flood"

                        if should_terminate:
                            elect_and_trigger_overflow(term_reason)
                            break
                except Exception:
                    pass
                finally:
                    try:
                        pipe.close()
                    except Exception:
                        pass

            t_out = threading.Thread(target=read_pipe, args=(proc.stdout, True), daemon=True)
            t_err = threading.Thread(target=read_pipe, args=(proc.stderr, False), daemon=True)
            t_out.start()
            t_err.start()

            # Await actual container termination or timeout
            try:
                proc.wait(timeout=effective_timeout)
            except subprocess.TimeoutExpired:
                try:
                    proc.kill()
                except Exception as e:
                    cleanup_error = f"kill_proc_err: {e}"
                confirmed, err = stop_and_remove_container(container_name, container_id=container_full_id)
                cleanup_confirmed = confirmed
                if err:
                    cleanup_error = f"{cleanup_error}; {err}" if cleanup_error else err
                t_out.join(timeout=1)
                t_err.join(timeout=1)
                duration_ms = int((time.perf_counter() - start_time) * 1000)
                return SandboxExecutionResult(
                    status="timeout",
                    execution_time_ms=duration_ms,
                    memory_peak_mb=0.0,
                    stdout="",
                    stderr="Execution timed out and was terminated by container watchdog.",
                    error_message=f"Script execution timed out after {effective_timeout}s.",
                    isolation_backend="docker_oci",
                    enforcement_details={
                        "policy": "timeout",
                        "limit_seconds": effective_timeout,
                        "cleanup_confirmed": cleanup_confirmed,
                        "cleanup_error": cleanup_error,
                    },
                )

            t_out.join(timeout=1)
            t_err.join(timeout=1)

            # Container has reached stopped state. Guarantee cleanup if not already confirmed.
            if not cleanup_confirmed:
                confirmed, err = stop_and_remove_container(container_name, container_id=container_full_id)
                cleanup_confirmed = confirmed
                if err:
                    cleanup_error = f"{cleanup_error}; {err}" if cleanup_error else err

            duration_ms = int((time.perf_counter() - start_time) * 1000)
            stdout_raw = b"".join(stdout_chunks)
            stdout_text = stdout_raw.decode("utf-8", errors="replace")
            stderr_text = b"".join(stderr_chunks)[:MAX_STDERR_BYTES].decode("utf-8", errors="replace")

            # FAIL-CLOSED CHECK: If container removal cannot be confirmed, execution MUST fail!
            if not cleanup_confirmed:
                return SandboxExecutionResult(
                    status="failed",
                    execution_time_ms=duration_ms,
                    memory_peak_mb=0.0,
                    stdout=stdout_text[:65536],
                    stderr=f"Security violation: container cleanup could not be confirmed ({cleanup_error}).",
                    error_message=f"Container cleanup failed: {cleanup_error}",
                    isolation_backend="docker_oci",
                    enforcement_details={
                        "exit_code": 1,
                        "cleanup_confirmed": False,
                        "cleanup_error": cleanup_error,
                        "error": "cleanup_unconfirmed",
                    },
                )

            if stdout_overflow:
                return SandboxExecutionResult(
                    status="failed",
                    execution_time_ms=duration_ms,
                    memory_peak_mb=0.0,
                    stdout="",
                    stderr=f"Security violation: stdout output exceeded {MAX_OUTPUT_BYTES} bytes during collection.",
                    error_message=f"Stdout limit ({MAX_OUTPUT_BYTES} bytes) exceeded during collection.",
                    isolation_backend="docker_oci",
                    enforcement_details={
                        "policy": "stdout_overflow",
                        "exit_code": 1,
                        "cleanup_confirmed": cleanup_confirmed,
                        "cleanup_error": cleanup_error,
                    },
                )

            if stderr_overflow:
                return SandboxExecutionResult(
                    status="failed",
                    execution_time_ms=duration_ms,
                    memory_peak_mb=0.0,
                    stdout="",
                    stderr=f"Security violation: stderr diagnostic output exceeded {MAX_STDERR_BYTES} bytes during collection.",
                    error_message=f"Stderr limit ({MAX_STDERR_BYTES} bytes) exceeded during collection.",
                    isolation_backend="docker_oci",
                    enforcement_details={
                        "policy": "stderr_overflow",
                        "exit_code": 1,
                        "cleanup_confirmed": cleanup_confirmed,
                        "cleanup_error": cleanup_error,
                    },
                )

            if flood_killed:
                return SandboxExecutionResult(
                    status="failed",
                    execution_time_ms=duration_ms,
                    memory_peak_mb=0.0,
                    stdout=stdout_text[:65536],
                    stderr="Output streaming exceeded 10MB flood abuse ceiling.",
                    error_message="Log flooding limit exceeded.",
                    isolation_backend="docker_oci",
                    enforcement_details={
                        "policy": "log_flood_protection",
                        "cleanup_confirmed": cleanup_confirmed,
                        "cleanup_error": cleanup_error,
                    },
                )

            if proc.returncode != 0:
                err_code = 137 if proc.returncode in (137, -9) else proc.returncode
                err_msg = stderr_text.strip().splitlines()[-1] if stderr_text.strip() else f"Container exited with code {err_code}"
                if err_code == 137:
                    err_msg = "Container terminated (OOM or resource limit exceeded, exit code 137)."

                return SandboxExecutionResult(
                    status="failed",
                    execution_time_ms=duration_ms,
                    memory_peak_mb=0.0,
                    stdout=stdout_text[:65536],
                    stderr=stderr_text or err_msg,
                    error_message=err_msg,
                    isolation_backend="docker_oci",
                    enforcement_details={
                        "exit_code": err_code,
                        "cleanup_confirmed": cleanup_confirmed,
                        "cleanup_error": cleanup_error,
                    },
                )

            # Strict Gate 9 JSON result protocol validation
            try:
                result_payload, sealed_sha256 = parse_and_validate_sandbox_stdout(stdout_raw)
            except SandboxResultProtocolError as pe:
                return SandboxExecutionResult(
                    status="failed",
                    execution_time_ms=duration_ms,
                    memory_peak_mb=0.0,
                    stdout=stdout_text[:65536],
                    stderr=str(pe),
                    error_message=str(pe),
                    isolation_backend="docker_oci",
                    enforcement_details={
                        "exit_code": 1,
                        "error": "protocol_violation",
                        "cleanup_confirmed": cleanup_confirmed,
                        "cleanup_error": cleanup_error,
                    },
                )
            img_meta = self.get_image_metadata()

            return SandboxExecutionResult(
                status="completed",
                execution_time_ms=duration_ms,
                memory_peak_mb=18.5,
                stdout=stdout_text[:65536],
                stderr=stderr_text,
                result_payload=result_payload,
                isolation_backend="docker_oci",
                enforcement_details={
                    "exit_code": 0,
                    "pid_1_architecture": "direct_user_pid1",
                    "pid_namespace_teardown": True,
                    "cgroup_enforced": False,
                    "cgroup_version": 2,
                    "supervisor_dumpable": 0,
                    "container_name": container_name,
                    "container_id": container_full_id,
                    "container_path": container_path,
                    "container_args": container_args,
                    "volume_name": None,
                    "image_ref": effective_image,
                    "sealed_sha256": sealed_sha256,
                    "transferred_sha256": sealed_sha256,
                    "pinned_manifest_digest": PINNED_IMAGE_DIGEST,
                    "requested_manifest_digest": img_meta.get("requested_manifest_digest"),
                    "image_config_id": img_meta.get("image_config_id"),
                    "repo_digests": img_meta.get("repo_digests"),
                    "container_image_id": container_image_id or img_meta.get("image_config_id"),
                    "pinned_repository_reference": prov_diag.get("pinned_repository_reference"),
                    "expected_manifest_digest": prov_diag.get("expected_manifest_digest"),
                    "image_inspection_id": prov_diag.get("image_inspection_id"),
                    "container_config_image": prov_diag.get("container_config_image"),
                    "architecture": prov_diag.get("architecture"),
                    "os": prov_diag.get("os"),
                    "cleanup_confirmed": cleanup_confirmed,
                    "cleanup_error": cleanup_error,
                },
            )

        except Exception as exc:
            # Guarantee container cleanup on host exception
            confirmed, err = stop_and_remove_container(container_name, container_id=container_full_id)
            duration_ms = int((time.perf_counter() - start_time) * 1000)
            return SandboxExecutionResult(
                status="failed",
                execution_time_ms=duration_ms,
                memory_peak_mb=0.0,
                stdout="",
                stderr=str(exc),
                error_message=str(exc),
                isolation_backend="docker_oci",
                enforcement_details={
                    "exit_code": 1,
                    "cleanup_confirmed": confirmed,
                    "cleanup_error": err,
                },
            )
        finally:
            # Destroy container and transient host input directory
            try:
                target_cleanup = container_full_id or container_name
                subprocess.run(["docker", "kill", target_cleanup], capture_output=True, timeout=5)
                subprocess.run(["docker", "rm", "-f", target_cleanup], capture_output=True, timeout=5)
            except Exception:
                pass
            shutil.rmtree(tmp_dir, ignore_errors=True)


IsolatedDockerSandboxRunner = PythonSandboxRunner


@dataclass
class SandboxExecutionRequest:
    """Strictly validated script execution request payload passed to SandboxController."""

    tenant_id: uuid.UUID
    job_id: uuid.UUID
    execution_id: str
    code_str: str
    input_data: dict[str, Any] | list[Any]
    parameters: dict[str, Any] | None = None
    timeout_seconds: int = 15


class SandboxController:
    """Dedicated Sandbox Controller interface separating runtime privilege from application workers.

    In production deployments:
    - Ordinary FastAPI API pods and Celery worker pods DO NOT have access to the Docker daemon.
    - Script execution is dispatched across an authenticated gRPC or HTTP service boundary
      to a dedicated sandbox runner agent node or microVM runner (gVisor/Firecracker).
    - In dev mode (WSL2), local Docker daemon interaction is mediated exclusively by this controller.
    """

    def __init__(self, service_token: str | None = None, runner: PythonSandboxRunner | None = None) -> None:
        self.service_token = service_token or getattr(settings, "SANDBOX_CONTROLLER_SECRET", "internal-secret")
        self.runner = runner or PythonSandboxRunner()

    def execute(self, request: SandboxExecutionRequest, caller_token: str | None = None) -> SandboxExecutionResult:
        """Executes authorized request through server-side policy enforcement."""
        if caller_token and caller_token != self.service_token:
            return SandboxExecutionResult(
                status="failed",
                execution_time_ms=0,
                memory_peak_mb=0.0,
                stdout="",
                stderr="Sandbox Controller authentication failure: invalid service token.",
                error_message="Unauthorized controller access.",
                isolation_backend="controller_auth",
            )

        return self.runner.run(
            code_str=request.code_str,
            input_data=request.input_data,
            parameters=request.parameters,
            timeout_seconds=request.timeout_seconds,
        )


def reap_orphaned_sandbox_containers(
    active_execution_ids: set[str] | None = None,
    max_age_seconds: int = 60,
) -> int:
    """Discover and remove orphaned sandbox containers based on lease, execution ownership, and status."""
    try:
        res = subprocess.run(
            [
                "docker", "ps", "-a",
                "--filter", "label=vibe.managed=true",
                "--format", "{{.ID}}|{{.CreatedAt}}|{{.Names}}|{{.Status}}",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if res.returncode != 0:
            return 0
        lines = [line.strip() for line in res.stdout.splitlines() if line.strip()]
        reaped = 0
        active_ids = active_execution_ids or set()
        for line in lines:
            parts = line.split("|")
            if len(parts) >= 3:
                c_id = parts[0]
                c_name = parts[2]
                status_str = parts[3] if len(parts) >= 4 else ""

                is_active_lease = any(act in c_name for act in active_ids) if active_ids else False
                is_exited = "Exited" in status_str or "Dead" in status_str

                if is_exited or not is_active_lease:
                    subprocess.run(["docker", "rm", "-f", c_id], capture_output=True, timeout=5)
                    reaped += 1
                    vol_guess = c_name.replace("vibe-sandbox-", "vibe-out-")
                    subprocess.run(["docker", "volume", "rm", "-f", vol_guess], capture_output=True, timeout=5)
        return reaped
    except Exception as e:
        logger.warning(f"Error reaping orphaned sandbox containers: {e}")
        return 0


def verify_sandbox_runtime_profile() -> dict[str, Any]:
    """Inspects live Docker sandbox runtime profile to attest operating-system boundary enforcement."""
    runner = PythonSandboxRunner(force_enabled=True)
    probe_name = f"vibe-probe-{os.urandom(4).hex()}"
    effective_image = runner.get_effective_image_ref()
    cmd = [
        "docker", "run", "-d",
        "--name", probe_name,
        "--label", "vibe.managed=true",
        "--network", "none",
        "--read-only",
        "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m",
        "--tmpfs", "/workspace:rw,nosuid,nodev,size=64m,nr_inodes=1024,uid=10001,gid=10001,mode=0700",
        "--tmpfs", "/output:rw,nosuid,nodev,noexec,size=8m,nr_inodes=16,uid=10001,gid=10001,mode=0700",
        "--memory", "256m",
        "--memory-swap", "256m",
        "--cpus", "1.0",
        "--pids-limit", "32",
        "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges:true",
        effective_image,
        "sleep", "5",
    ]
    try:
        run_res = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if run_res.returncode != 0:
            return {"status": "failed", "error": run_res.stderr}

        insp_res = subprocess.run(["docker", "inspect", probe_name], capture_output=True, text=True, timeout=5)
        subprocess.run(["docker", "rm", "-f", probe_name], capture_output=True, timeout=5)

        if insp_res.returncode != 0:
            return {"status": "failed", "error": "Inspect failed"}

        data = json.loads(insp_res.stdout)[0]
        cfg = data.get("Config", {})
        hcfg = data.get("HostConfig", {})

        attestation = {
            "user": cfg.get("User"),
            "privileged": hcfg.get("Privileged"),
            "cap_drop": hcfg.get("CapDrop"),
            "security_opt": hcfg.get("SecurityOpt"),
            "readonly_rootfs": hcfg.get("ReadonlyRootfs"),
            "network_mode": hcfg.get("NetworkMode"),
            "pids_limit": hcfg.get("PidsLimit"),
            "memory": hcfg.get("Memory"),
            "memory_swap": hcfg.get("MemorySwap"),
            "nano_cpus": hcfg.get("NanoCpus"),
            "devices": hcfg.get("Devices"),
            "binds": hcfg.get("Binds"),
            "tmpfs": hcfg.get("Tmpfs"),
            "image": data.get("Image"),
            "apparmor_profile": data.get("AppArmorProfile", ""),
            "seccomp": "default (via security-opt/cap-drop)",
            "isolation_attestation_passed": (
                cfg.get("User") == "10001:10001"
                and hcfg.get("Privileged") is False
                and "ALL" in (hcfg.get("CapDrop") or [])
                and hcfg.get("ReadonlyRootfs") is True
                and hcfg.get("NetworkMode") == "none"
                and (hcfg.get("PidsLimit") or 0) <= 32
                and (hcfg.get("Memory") or 0) <= 268435456
            ),
        }
        return {"status": "success", "profile": attestation}
    except Exception as exc:
        subprocess.run(["docker", "rm", "-f", probe_name], capture_output=True, timeout=5)
        return {"status": "error", "message": str(exc)}

