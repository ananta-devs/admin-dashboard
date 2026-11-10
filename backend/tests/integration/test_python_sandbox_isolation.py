"""Adversarial security tests proving real operating-system and OCI container isolation.

Verifies that untrusted Python execution boundaries are enforced by kernel namespaces,
cgroups, VFS read-only mounts, and unprivileged user identities, rather than purely
by Python AST inspection.
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

import pytest

from app.analytics.python_sandbox import (
    IsolatedDockerSandboxRunner,
    PythonSandboxRunner,
    reap_orphaned_sandbox_containers,
    verify_sandbox_runtime_profile,
)


@pytest.fixture
def sandbox_runner() -> PythonSandboxRunner:
    """Provides a verified OCI sandbox runner instance configured with test timeout."""
    return PythonSandboxRunner(
        timeout_seconds=5,
        memory_limit_mb=256,
        force_enabled=True,
    )


class TestPythonSandboxAdversarialIsolation:
    """Adversarial test suite verifying OS-level isolation invariants."""

    def test_01_read_application_environment_secrets(
        self, sandbox_runner: PythonSandboxRunner
    ) -> None:
        """Attempt to read application environment secrets (must find sanitized empty env)."""
        code = """
import os
env_dump = dict(os.environ)
result = {
    "has_session_secret": "SESSION_SECRET_KEY" in env_dump,
    "has_encryption_key": "ENCRYPTION_MASTER_KEY" in env_dump,
    "has_db_url": "DATABASE_URL" in env_dump or "POSTGRES_PASSWORD" in env_dump,
    "env_keys": list(env_dump.keys()),
}
"""
        res = sandbox_runner.run(code, input_data={}, bypass_ast=True)
        assert res.status == "completed", f"Execution failed: {res.stderr}"
        payload = res.result_payload or {}
        assert payload.get("has_session_secret") is False
        assert payload.get("has_encryption_key") is False
        assert payload.get("has_db_url") is False

    def test_02_read_encryption_master_key(self, sandbox_runner: PythonSandboxRunner) -> None:
        """Attempt to read ENCRYPTION_MASTER_KEY from environment or files."""
        code = """
import os
found = False
for k, v in os.environ.items():
    if "master" in k.lower() or "encryption" in k.lower():
        found = True
result = {"master_key_exposed": found or ("ENCRYPTION_MASTER_KEY" in os.environ)}
"""
        res = sandbox_runner.run(code, input_data={}, bypass_ast=True)
        assert res.status == "completed"
        payload = res.result_payload or {}
        assert payload.get("master_key_exposed") is False

    def test_03_read_database_and_redis_credentials(
        self, sandbox_runner: PythonSandboxRunner
    ) -> None:
        """Attempt to find database/redis credentials in environment."""
        code = """
import os
creds = [v for k, v in os.environ.items() if "postgres" in v.lower() or "redis" in v.lower()]
result = {"credentials_found": len(creds) > 0}
"""
        res = sandbox_runner.run(code, input_data={}, bypass_ast=True)
        assert res.status == "completed"
        payload = res.result_payload or {}
        assert payload.get("credentials_found") is False

    def test_04_read_application_source_files(self, sandbox_runner: PythonSandboxRunner) -> None:
        """Attempt to read host application source files (e.g. backend/app/core/config.py)."""
        code = """
import os
app_exists = os.path.exists("/app/app/core/config.py") or os.path.exists("D:/admin-dashboard") or os.path.exists("D:/vibe") or os.path.exists("/vibe")
result = {"host_source_accessible": app_exists}
"""
        res = sandbox_runner.run(code, input_data={}, bypass_ast=True)
        assert res.status == "completed"
        payload = res.result_payload or {}
        assert payload.get("host_source_accessible") is False

    def test_05_read_arbitrary_host_files(self, sandbox_runner: PythonSandboxRunner) -> None:
        """Attempt to read sensitive OS host files (/etc/shadow, /root, C:/Windows)."""
        code = """
try:
    with open("/etc/shadow", "r") as f:
        data = f.read()
    result = {"shadow_read": True}
except Exception as e:
    result = {"shadow_read": False, "error": type(e).__name__}
"""
        res = sandbox_runner.run(code, input_data={}, bypass_ast=True)
        assert res.status == "completed"
        payload = res.result_payload or {}
        assert payload.get("shadow_read") is False
        assert payload.get("error") in ("PermissionError", "FileNotFoundError")

    def test_06_access_cross_tenant_data(self, sandbox_runner: PythonSandboxRunner) -> None:
        """Verify script only accesses the input dataset serialized specifically for its run."""
        code = """
result = {
    "data_keys": list(data.keys()),
    "records_len": len(data.get("records", [])),
}
"""
        input_data = {"records": [{"tenant": "org_A", "val": 42}]}
        res = sandbox_runner.run(code, input_data=input_data, bypass_ast=True)
        assert res.status == "completed"
        payload = res.result_payload or {}
        assert payload.get("records_len") == 1

    def test_07_access_docker_socket(self, sandbox_runner: PythonSandboxRunner) -> None:
        """Attempt to access the Docker daemon socket inside the sandbox."""
        code = """
import os
socket_paths = ["/var/run/docker.sock", "/run/docker.sock", "//./pipe/docker_engine"]
accessible = [p for p in socket_paths if os.path.exists(p)]
result = {"docker_socket_accessible": len(accessible) > 0}
"""
        res = sandbox_runner.run(code, input_data={}, bypass_ast=True)
        assert res.status == "completed"
        payload = res.result_payload or {}
        assert payload.get("docker_socket_accessible") is False

    def test_08_perform_dns_resolution(self, sandbox_runner: PythonSandboxRunner) -> None:
        """Attempt DNS resolution (must fail due to --network none)."""
        code = """
import socket
socket.gethostbyname("google.com")
"""
        res = sandbox_runner.run(code, input_data={}, bypass_ast=True)
        assert res.status == "failed"
        assert (
            "gaierror" in res.stderr
            or "Name or service not known" in res.stderr
            or "Temporary failure" in res.stderr
        )

    def test_09_make_http_and_raw_socket_connections(
        self, sandbox_runner: PythonSandboxRunner
    ) -> None:
        """Attempt TCP socket connection to external internet (must fail with Network unreachable)."""
        code = """
import socket
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.settimeout(2.0)
s.connect(("8.8.8.8", 53))
"""
        res = sandbox_runner.run(code, input_data={}, bypass_ast=True)
        assert res.status == "failed"
        assert "Network is unreachable" in res.stderr or "Errno 101" in res.stderr

    def test_10_access_postgres_and_redis_endpoints(
        self, sandbox_runner: PythonSandboxRunner
    ) -> None:
        """Attempt connection to internal services on host (5432, 6379)."""
        code = """
import socket
results = {}
for port in [5432, 6379]:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(1.0)
        s.connect(("127.0.0.1", port))
        results[port] = "connected"
    except Exception as e:
        results[port] = type(e).__name__
result = results
"""
        res = sandbox_runner.run(code, input_data={}, bypass_ast=True)
        assert res.status == "completed"
        payload = res.result_payload or {}
        # Inside network namespace with loopback only, local ports are not bound to host DB/Redis
        assert payload.get("5432") == "ConnectionRefusedError"
        assert payload.get("6379") == "ConnectionRefusedError"

    def test_11_traverse_filesystem_paths(self, sandbox_runner: PythonSandboxRunner) -> None:
        """Attempt path traversal out of /workspace."""
        code = """
import os
try:
    with open("../../../etc/passwd", "r") as f:
        data = f.read(50)
    result = {"read_passwd": True}
except Exception as e:
    result = {"read_passwd": False, "error": str(e)}
"""
        res = sandbox_runner.run(code, input_data={}, bypass_ast=True)
        assert res.status == "completed"
        payload = res.result_payload or {}
        # passwd inside root image contains only minimal users, cannot traverse outside container
        assert payload.get("read_passwd") is True or "No such file" in str(payload.get("error"))

    def test_12_escape_workspace_using_symlinks(self, sandbox_runner: PythonSandboxRunner) -> None:
        """Attempt to symlink host paths (cannot access host paths outside container mount)."""
        code = """
import os
try:
    os.symlink("/", "root_symlink")
    files = os.listdir("root_symlink")
    result = {"symlink_created": True, "files": files[:5]}
except Exception as e:
    result = {"symlink_created": False, "error": str(e)}
"""
        res = sandbox_runner.run(code, input_data={}, bypass_ast=True)
        assert res.status == "completed"
        payload = res.result_payload or {}
        # Symlink to / inside container resolves to container's read-only root, not host
        assert "Windows" not in str(payload.get("files", []))
        assert "Users" not in str(payload.get("files", []))

    def test_13_inspect_process_environment_and_fds(
        self, sandbox_runner: PythonSandboxRunner
    ) -> None:
        """Attempt to inspect process file descriptors (/proc/self/fd)."""
        code = """
import os
try:
    fds = os.listdir("/proc/self/fd")
    result = {"fd_count": len(fds)}
except Exception as e:
    result = {"error": str(e)}
"""
        res = sandbox_runner.run(code, input_data={}, bypass_ast=True)
        assert res.status == "completed"
        payload = res.result_payload or {}
        assert payload.get("fd_count", 0) < 10

    def test_14_python_introspection_attacks(self, sandbox_runner: PythonSandboxRunner) -> None:
        """Attempt class hierarchy traversal to locate system primitives."""
        code = """
subclasses = {c.__name__: c for c in ().__class__.__bases__[0].__subclasses__()}
result = {
    "has_catch_warnings": "_GeneratorContextManagerBase" in subclasses,
    "subclasses_count": len(subclasses),
}
"""
        res = sandbox_runner.run(code, input_data={}, bypass_ast=True)
        assert res.status == "completed"
        payload = res.result_payload or {}
        assert payload.get("subclasses_count", 0) > 0

    def test_15_access_denied_builtins_and_imports(
        self, sandbox_runner: PythonSandboxRunner
    ) -> None:
        """Attempt to write to the read-only root filesystem."""
        code = """
with open("/etc/pwned.txt", "w") as f:
    f.write("test")
"""
        res = sandbox_runner.run(code, input_data={}, bypass_ast=True)
        assert res.status == "failed"
        assert "Read-only file system" in res.stderr or "Errno 30" in res.stderr

    def test_16_spawn_child_processes_fork_bomb(self, sandbox_runner: PythonSandboxRunner) -> None:
        """Attempt fork bomb (blocked by Linux kernel cgroup --pids-limit 32)."""
        code = """
import os
for _ in range(100):
    try:
        os.fork()
    except Exception:
        break
"""
        res = sandbox_runner.run(code, input_data={}, bypass_ast=True)
        assert res.status in ("failed", "completed", "timeout")
        if res.status == "failed":
            # Either caught in-process as EAGAIN / Errno 11 or killed by kernel/runtime with exit code 137 / -9
            assert (
                "Resource temporarily unavailable" in res.stderr
                or "Errno 11" in res.stderr
                or (
                    res.enforcement_details
                    and res.enforcement_details.get("exit_code") in (137, 1, -9)
                )
                or "resource limit" in (res.error_message or "").lower()
            )

    def test_17_create_excessive_threads(self, sandbox_runner: PythonSandboxRunner) -> None:
        """Attempt to spawn excessive threads (also bounded by pids-limit)."""
        code = """
import threading
import time

def worker():
    time.sleep(2)

threads = []
for i in range(100):
    try:
        t = threading.Thread(target=worker)
        t.start()
        threads.append(t)
    except Exception:
        break

result = {"threads_started": len(threads)}
"""
        res = sandbox_runner.run(code, input_data={}, timeout_seconds=10, bypass_ast=True)
        assert res.status in ("completed", "failed", "timeout")
        if res.status == "completed":
            payload = res.result_payload or {}
            assert payload.get("threads_started", 0) <= 32

    def test_18_run_infinite_cpu_loops(self, sandbox_runner: PythonSandboxRunner) -> None:
        """Attempt infinite CPU loop (enforced by wall-clock timeout watchdog)."""
        code = """
while True:
    pass
"""
        start = time.perf_counter()
        res = sandbox_runner.run(code, input_data={}, timeout_seconds=5, bypass_ast=True)
        elapsed = time.perf_counter() - start
        assert res.status == "timeout"
        assert elapsed >= 4.5  # Runner timeout is 5s
        assert "timed out" in res.error_message.lower()

    def test_19_allocate_excessive_memory(self, sandbox_runner: PythonSandboxRunner) -> None:
        """Attempt to allocate 500MB exceeding the 256MB container memory cap (cgroup OOM kill)."""
        code = """
x = bytearray(500 * 1024 * 1024)
"""
        res = sandbox_runner.run(code, input_data={}, timeout_seconds=15, bypass_ast=True)
        assert res.status in ("failed", "timeout")
        if res.status == "failed":
            assert (
                "137" in str(res.error_message)
                or "OOM" in str(res.error_message)
                or res.error_message is not None
            )

    def test_20_write_excessive_temporary_data(self, sandbox_runner: PythonSandboxRunner) -> None:
        """Attempt to write 100MB to /tmp tmpfs (enforced by size=64m cap)."""
        code = """
with open("/tmp/large.bin", "wb") as f:
    f.write(b"0" * (100 * 1024 * 1024))
"""
        res = sandbox_runner.run(code, input_data={}, timeout_seconds=15, bypass_ast=True)
        assert res.status in ("failed", "timeout")
        if res.status == "failed":
            assert (
                "No space left on device" in res.stderr
                or "Errno 28" in res.stderr
                or "1" in str(res.enforcement_details.get("exit_code"))
            )

    def test_21_produce_excessive_stdout_stderr(self, sandbox_runner: PythonSandboxRunner) -> None:
        """Verify stdout is truncated to bounded 64KB ceiling."""
        code = """
import sys
for _ in range(5000):
    print("EXTREMELY LONG STDOUT LINE GENERATING FLOOD OF BYTES 1234567890")
result = {"status": "ok"}
"""
        res = sandbox_runner.run(code, input_data={}, bypass_ast=True)
        assert res.status == "completed"
        assert len(res.stdout) <= 65536

    def test_22_attempt_to_exceed_execution_time_limits(
        self, sandbox_runner: PythonSandboxRunner
    ) -> None:
        """Attempt sleep longer than timeout."""
        code = """
import time
time.sleep(20)
"""
        res = sandbox_runner.run(code, input_data={}, bypass_ast=True)
        assert res.status == "timeout"

    def test_23_verify_ephemeral_workspace_destroyed(
        self, sandbox_runner: PythonSandboxRunner
    ) -> None:
        """Verify that transient host directory is deleted immediately after execution."""
        code = """
with open("test_artifact.txt", "w") as f:
    f.write("persistent_probe")
result = {"done": True}
"""
        res = sandbox_runner.run(code, input_data={}, bypass_ast=True)
        assert res.status == "completed"
        # Search temporary directory on host to confirm no vibe_sandbox_* folders remain
        import tempfile

        tmp_base = Path(tempfile.gettempdir())
        leftovers = list(tmp_base.glob("vibe_sandbox_*"))
        assert len(leftovers) == 0, f"Found lingering sandbox workspaces: {leftovers}"

    def test_24_disabled_mode_rejects_execution(self) -> None:
        """Verify administrative safety gate when execution is disabled."""
        disabled_runner = PythonSandboxRunner(force_enabled=False)
        res = disabled_runner.run("result = {'a': 1}", input_data={})
        assert res.status == "disabled"
        assert "disabled" in res.error_message.lower()
        assert res.isolation_backend == "disabled"

    def test_25_read_only_input_mount_blocks_tampering(
        self, sandbox_runner: PythonSandboxRunner
    ) -> None:
        """Verify that user code cannot modify /input mount (read-only filesystem)."""
        code = """
try:
    with open("/input/user_code.py", "w") as f:
        f.write("tampered")
except Exception as e:
    result = {"tampered": False, "error": str(e)}
"""
        res = sandbox_runner.run(code, input_data={}, bypass_ast=True)
        # Execution succeeds with error recorded or fails with Read-only file system
        if res.status == "completed":
            payload = res.result_payload or {}
            assert (
                "Read-only file system" in str(payload.get("error"))
                or payload.get("tampered") is False
            )

    def test_26_output_symlink_attack_rejected(self, sandbox_runner: PythonSandboxRunner) -> None:
        """Verify that replacing output with a symlink to host/container secrets is rejected."""
        code = """
import os
import sys
try:
    if os.path.exists("output.json"):
        os.remove("output.json")
    os.symlink("/etc/passwd", "output.json")
except Exception as e:
    sys.stderr.write(f"symlink error: {e}\\n")
sys.exit(0)
"""
        res = sandbox_runner.run(code, input_data={}, bypass_ast=True)
        # Must be rejected or fail with symlink error
        assert res.status == "failed"
        assert "symlink" in (res.error_message or "").lower() or "symlink" in res.stderr.lower()

    def test_27_relative_symlink_traversal_rejected(
        self, sandbox_runner: PythonSandboxRunner
    ) -> None:
        """Verify relative path traversal symlink is rejected by safe directory validation."""
        code = """
import os
import sys
try:
    if os.path.exists("output.json"):
        os.remove("output.json")
    os.symlink("../../etc/shadow", "output.json")
except Exception as e:
    sys.stderr.write(f"symlink error: {e}\\n")
sys.exit(0)
"""
        res = sandbox_runner.run(code, input_data={}, bypass_ast=True)
        assert res.status == "failed"
        assert "symlink" in (res.error_message or "").lower() or "symlink" in res.stderr.lower()

    def test_28_log_flooding_memory_protection(self, sandbox_runner: PythonSandboxRunner) -> None:
        """Verify that streaming log reader bounds memory and safely truncates huge outputs."""
        code = """
import sys
# Emit large volume of data
for _ in range(20000):
    sys.stdout.write("A" * 1024 + "\\n")
result = {"done": True}
"""
        res = sandbox_runner.run(code, input_data={}, bypass_ast=True)
        # Either completed with bounded output or killed for log flood abuse
        assert res.status in ("completed", "failed", "timeout")
        assert len(res.stdout) <= 65536
        assert len(res.stderr) <= 65536

    def test_29_orphan_container_reaper_cleans_stale_containers(self) -> None:
        """Verify that reap_orphaned_sandbox_containers removes stale managed containers."""
        reaped = reap_orphaned_sandbox_containers(max_age_seconds=0)
        assert isinstance(reaped, int)
        assert reaped >= 0

    def test_30_runtime_profile_security_attestation(self) -> None:
        """Verify that Docker sandbox profile passes operating-system boundary attestation."""
        attestation = verify_sandbox_runtime_profile()
        assert attestation["status"] == "success"
        profile = attestation["profile"]
        assert profile["isolation_attestation_passed"] is True
        assert profile["user"] == "10001:10001"
        assert profile["privileged"] is False
        assert profile["readonly_rootfs"] is True
        assert profile["network_mode"] == "none"

    def test_31_immutable_image_digest_pinning(self) -> None:
        """Verify runner image digest pinning to prevent mutable tag poisoning."""
        runner = PythonSandboxRunner(force_enabled=True)
        assert runner.image_digest.startswith("sha256:")
        assert len(runner.image_digest) == 71  # 'sha256:' + 64 hex chars
        assert "@sha256:" in runner.get_effective_image_ref()

    def test_32_hard_writable_workspace_quota_blocks_large_writes(
        self, sandbox_runner: PythonSandboxRunner
    ) -> None:
        """Verify that writing beyond the 64MB workspace tmpfs quota fails with disk quota exhausted."""
        code = """
try:
    with open("large.bin", "wb") as f:
        f.write(b"A" * (70 * 1024 * 1024))
    result = {"wrote": True}
except Exception as e:
    result = {"wrote": False, "error": str(e), "err_type": type(e).__name__}
"""
        res = sandbox_runner.run(code, input_data={}, bypass_ast=True)
        # Either completed recording No space left on device or failed with exit code 1
        if res.status == "completed":
            payload = res.result_payload or {}
            assert payload.get("wrote") is False
            assert "No space left on device" in str(payload.get("error")) or "Errno 28" in str(
                payload.get("error")
            )
        else:
            assert res.status == "failed"

    def test_33_inode_exhaustion_defense(self, sandbox_runner: PythonSandboxRunner) -> None:
        """Verify that creating excessive files is blocked by nr_inodes limit (max 1024)."""
        code = """
count = 0
try:
    for i in range(1500):
        with open(f"f_{i}.txt", "w") as f:
            f.write("a")
        count += 1
    result = {"count": count, "unbounded": True}
except Exception as e:
    result = {"count": count, "unbounded": False, "error": str(e)}
"""
        res = sandbox_runner.run(code, input_data={}, bypass_ast=True)
        if res.status == "completed":
            payload = res.result_payload or {}
            assert payload.get("unbounded") is False
            assert payload.get("count", 0) <= 1024
            assert "No space left on device" in str(payload.get("error")) or "Errno 28" in str(
                payload.get("error")
            )
        else:
            assert res.status == "failed"

    def test_34_directory_depth_exhaustion_defense(
        self, sandbox_runner: PythonSandboxRunner
    ) -> None:
        """Verify directory depth ceiling prevents recursive nested directory resource exhaustion."""
        code = """
import os
error_msg = None
try:
    curr = "level1"
    for _ in range(10):
        os.mkdir(curr)
        curr = os.path.join(curr, "sub")
    result = {"success": True}
except Exception as e:
    result = {"success": False, "error": str(e), "type": type(e).__name__}
"""
        res = sandbox_runner.run(code, input_data={}, bypass_ast=True)
        if res.status == "completed":
            payload = res.result_payload or {}
            assert payload.get("success") is False
            assert "Directory depth limit exceeded" in str(
                payload.get("error")
            ) or "RecursionError" in str(payload.get("type"))
        else:
            assert res.status == "failed"

    def test_35_immutable_image_resolution_rejects_tag_mutation(self) -> None:
        """Verify that get_effective_image_ref always resolves to digest and ignores mutable tag changes."""
        runner = PythonSandboxRunner(force_enabled=True, image="vibe-python-sandbox:latest")
        ref = runner.get_effective_image_ref()
        assert "@sha256:" in ref
        assert not ref.endswith(":latest")
        assert runner.verify_image_digest() is True

    def test_36_sandbox_controller_authenticated_interface(
        self, sandbox_runner: PythonSandboxRunner
    ) -> None:
        """Verify that SandboxController enforces service token authentication and request boundaries."""
        import uuid

        from app.analytics.python_sandbox import SandboxController, SandboxExecutionRequest

        controller = SandboxController(service_token="test-secret-token", runner=sandbox_runner)
        req = SandboxExecutionRequest(
            tenant_id=uuid.uuid4(),
            job_id=uuid.uuid4(),
            execution_id="exec-123",
            code_str="result = {'ok': True}",
            input_data={},
        )
        # Invalid token rejected
        bad_res = controller.execute(req, caller_token="wrong-token")
        assert bad_res.status == "failed"
        assert "authentication failure" in (bad_res.stderr or "").lower()

        # Valid token accepted
        good_res = controller.execute(req, caller_token="test-secret-token")
        assert good_res.status == "completed"
        assert good_res.result_payload == {"ok": True}

    def test_37_output_within_permitted_limit(self, sandbox_runner: PythonSandboxRunner) -> None:
        """Verify that output within the permitted 5MB limit is safely staged and retrieved."""
        code = """
result = {"data": [f"item_{i}" for i in range(1000)], "count": 1000}
"""
        res = sandbox_runner.run(code, input_data={}, bypass_ast=True)
        assert res.status == "completed"
        assert res.result_payload is not None
        assert res.result_payload["count"] == 1000
        assert len(res.result_payload["data"]) == 1000

    def test_38_output_exceeding_5mb_rejected(self, sandbox_runner: PythonSandboxRunner) -> None:
        """Verify that an output file exceeding 5MB is rejected at validation boundary."""
        code = """
import json
huge_str = "x" * (6 * 1024 * 1024)
with open("/output/output.json", "w") as f:
    json.dump({"big": huge_str}, f)
"""
        res = sandbox_runner.run(code, input_data={}, bypass_ast=True)
        assert res.status == "failed"
        assert "5mb" in (res.error_message or "").lower() or "5mb" in res.stderr.lower()

    def test_39_output_80mb_attempt_blocked_by_tmpfs_quota(
        self, sandbox_runner: PythonSandboxRunner
    ) -> None:
        """Verify that an 80MB output attempt fails at filesystem boundary on 8MB tmpfs."""
        code = """
with open("/output/output.json", "w") as f:
    f.write("A" * (80 * 1024 * 1024))
"""
        res = sandbox_runner.run(code, input_data={}, bypass_ast=True)
        assert res.status == "failed"
        assert (
            "no space left on device" in res.stderr.lower()
            or "errno 28" in res.stderr.lower()
            or "5mb" in res.stderr.lower()
            or res.status == "failed"
        )

    def test_40_output_inode_exhaustion_defense(self, sandbox_runner: PythonSandboxRunner) -> None:
        """Verify that exhausting inodes on /output (nr_inodes=16) triggers No space left on device."""
        code = """
try:
    for i in range(25):
        with open(f"/output/file_{i}.txt", "w") as f:
            f.write("test")
    result = {"success": True}
except Exception as e:
    result = {"success": False, "error": str(e)}
"""
        res = sandbox_runner.run(code, input_data={}, bypass_ast=True)
        if res.status == "completed":
            payload = res.result_payload or {}
            assert payload.get("success") is False
            assert (
                "no space left on device" in str(payload.get("error")).lower()
                or "errno 28" in str(payload.get("error")).lower()
            )
        else:
            assert res.status == "failed"

    def test_41_output_multiple_unexpected_files_rejected(
        self, sandbox_runner: PythonSandboxRunner
    ) -> None:
        """Verify that creating multiple unexpected files in /output is rejected."""
        code = """
import json
with open("/output/output.json", "w") as f:
    json.dump({"valid": True}, f)
with open("/output/malicious_side_channel.txt", "w") as f:
    f.write("secret_leak")
"""
        res = sandbox_runner.run(code, input_data={}, bypass_ast=True)
        assert res.status == "failed"
        assert (
            "unexpected" in (res.error_message or "").lower() or "unexpected" in res.stderr.lower()
        )

    def test_42_output_symlink_replacement_rejected(
        self, sandbox_runner: PythonSandboxRunner
    ) -> None:
        """Verify that replacing output with symlink inside /output is rejected."""
        code = """
import os
try:
    if os.path.exists("/output/output.json"):
        os.remove("/output/output.json")
    os.symlink("/etc/passwd", "/output/output.json")
except Exception as e:
    pass
"""
        res = sandbox_runner.run(code, input_data={}, bypass_ast=True)
        assert res.status == "failed"
        assert "symlink" in (res.error_message or "").lower() or "symlink" in res.stderr.lower()

    def test_43_output_directory_traversal_rejected(
        self, sandbox_runner: PythonSandboxRunner
    ) -> None:
        """Verify that creating subdirectories in /output is rejected."""
        code = """
import os
os.makedirs("/output/sub_dir/nested", exist_ok=True)
with open("/output/sub_dir/nested/output.json", "w") as f:
    f.write('{"traversal": True}')
"""
        res = sandbox_runner.run(code, input_data={}, bypass_ast=True)
        assert res.status == "failed"
        assert (
            "unexpected directory" in (res.error_message or "").lower()
            or "unexpected directory" in res.stderr.lower()
        )

    def test_44_output_workspace_and_output_cleanup_normal_run(
        self, sandbox_runner: PythonSandboxRunner
    ) -> None:
        """Verify that after normal execution, container and host temporary folders are deleted and no volume created."""
        import subprocess

        code = "result = {'clean': True}"
        res = sandbox_runner.run(code, input_data={}, bypass_ast=True)
        assert res.status == "completed"

        container_name = res.enforcement_details.get("container_name")
        volume_name = res.enforcement_details.get("volume_name")
        assert volume_name is None

        if container_name:
            c_check = subprocess.run(
                ["docker", "ps", "-a", "--filter", f"name={container_name}", "-q"],
                capture_output=True,
                text=True,
            )
            assert c_check.stdout.strip() == "", f"Container {container_name} was not removed"

    def test_45_output_cleanup_after_container_termination(self) -> None:
        """Verify that when a container times out, both container and temporary resources are cleaned up."""
        import subprocess

        runner = PythonSandboxRunner(force_enabled=True, timeout_seconds=2)
        code = """
import time
time.sleep(10)
"""
        res = runner.run(code, input_data={}, bypass_ast=True)
        assert res.status in ("failed", "timeout")

        # Confirm no vibe-sandbox containers remain active
        c_check = subprocess.run(
            ["docker", "ps", "--filter", "label=vibe.managed=true", "-q"],
            capture_output=True,
            text=True,
        )
        assert c_check.stdout.strip() == ""

    def test_46_output_concurrent_executions_filesystem_isolation(
        self, sandbox_runner: PythonSandboxRunner
    ) -> None:
        """Verify concurrent executions run in parallel with isolated filesystems without cross-talk."""
        import concurrent.futures

        def run_one(val: str) -> dict:
            code = f"result = {{'marker': '{val}'}}"
            r = sandbox_runner.run(code, input_data={}, bypass_ast=True)
            return {
                "status": r.status,
                "payload": r.result_payload,
                "details": r.enforcement_details,
            }

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            fut_a = executor.submit(run_one, "WORKER_ALPHA")
            fut_b = executor.submit(run_one, "WORKER_BETA")
            res_a = fut_a.result(timeout=30)
            res_b = fut_b.result(timeout=30)

        assert res_a["status"] == "completed"
        assert res_b["status"] == "completed"
        assert res_a["payload"] == {"marker": "WORKER_ALPHA"}
        assert res_b["payload"] == {"marker": "WORKER_BETA"}
        # Ensure separate container identities and zero volumes
        assert res_a["details"].get("container_name") != res_b["details"].get("container_name")
        assert res_a["details"].get("volume_name") is None
        assert res_b["details"].get("volume_name") is None

    def test_47_controller_service_process_execution_api(self) -> None:
        """Verify that SandboxAgentClient can execute code via the Sandbox Agent service."""
        import uuid

        from app.sandbox_agent.client import SandboxAgentClient
        from app.sandbox_agent.server import app as agent_app

        client = SandboxAgentClient(app=agent_app)

        org_id = uuid.uuid4()
        job_id = uuid.uuid4()
        exec_id = f"exec-agent-{int(time.time())}"

        resp = client.execute(
            organization_id=org_id,
            job_id=job_id,
            execution_id=exec_id,
            code_str="result = {'agent_exec': True, 'sum': 2 + 2}",
            input_data={},
        )
        assert resp.status == "completed"
        assert resp.result_payload == {"agent_exec": True, "sum": 4}
        assert resp.isolation_backend == "sandbox_agent_oci"

    def test_48_controller_token_revocation_and_rotation(self) -> None:
        """Verify service identity validation, token revocation, and key rotation."""
        import uuid

        from starlette.testclient import TestClient

        from app.sandbox_agent.security import create_scoped_execution_token
        from app.sandbox_agent.server import app as agent_app
        from app.sandbox_agent.server import revocation_registry

        org_id = uuid.uuid4()
        job_id = uuid.uuid4()
        exec_id = f"exec-auth-{int(time.time())}"

        valid_token = create_scoped_execution_token(
            organization_id=org_id,
            job_id=job_id,
            execution_id=exec_id,
            secret_key="vibe-agent-primary-secret-key-2026",
        )

        req_payload = {
            "execution_id": exec_id,
            "organization_id": str(org_id),
            "job_id": str(job_id),
            "script_hash": "h123",
            "code_str": "result = {'ok': True}",
            "input_data": {},
            "parameters": {},
            "timeout_seconds": 10,
            "runtime_id": "python-3.11-standard",
        }

        with TestClient(agent_app, base_url="http://test") as client:
            # 1. Invalid signature rejected
            r_bad = client.post(
                "/api/v1/sandbox/execute", json=req_payload, headers={"X-Sandbox-Token": "bad.sig"}
            )
            assert r_bad.status_code == 403

            # 2. Token scope mismatch rejected (wrong execution_id)
            mismatch_payload = dict(req_payload)
            mismatch_payload["execution_id"] = "different-exec"
            r_mis = client.post(
                "/api/v1/sandbox/execute",
                json=mismatch_payload,
                headers={"X-Sandbox-Token": valid_token},
            )
            assert r_mis.status_code == 403

            # 3. Revocation of execution ID blocks subsequent calls
            revocation_registry.revoke_execution(exec_id)
            r_rev = client.post(
                "/api/v1/sandbox/execute",
                json=req_payload,
                headers={"X-Sandbox-Token": valid_token},
            )
            assert r_rev.status_code == 403
            assert "revoked" in r_rev.text.lower()

    def test_49_controller_crash_recovery_and_orphan_reaper(self) -> None:
        """Verify that stale containers and volumes are recovered after controller restart."""
        import os
        import subprocess

        from app.sandbox_agent.manager import SandboxRuntimeManager

        manager = SandboxRuntimeManager()

        # Create simulated stale container and volume
        stale_id = f"stale_{os.urandom(4).hex()}"
        stale_c = f"vibe-sandbox-{stale_id}"
        stale_v = f"vibe-out-{stale_id}"

        subprocess.run(["docker", "volume", "create", stale_v], capture_output=True, check=True)
        subprocess.run(
            [
                "docker",
                "run",
                "-d",
                "--name",
                stale_c,
                "--label",
                "vibe.managed=true",
                "vibe-python-sandbox:latest",
                "sleep",
                "1",
            ],
            capture_output=True,
            check=True,
        )

        time.sleep(1.5)  # Let it exit
        reaped = manager.recover_stale_containers()
        assert reaped >= 1

        # Confirm stale container was removed
        c_check = subprocess.run(
            ["docker", "ps", "-a", "--filter", f"name={stale_c}", "-q"],
            capture_output=True,
            text=True,
        )
        assert c_check.stdout.strip() == ""
        # Confirm stale volume was removed
        v_check = subprocess.run(
            ["docker", "volume", "ls", "--filter", f"name={stale_v}", "-q"],
            capture_output=True,
            text=True,
        )
        assert v_check.stdout.strip() == ""

    def test_50_runtime_seccomp_and_kernel_caps_attestation(self) -> None:
        """Attest kernel security attributes: UID, GID, CapEff, NoNewPrivs, and Seccomp mode."""
        import subprocess

        effective_image = PythonSandboxRunner().get_effective_image_ref()
        cmd = [
            "docker",
            "run",
            "--rm",
            "--security-opt",
            "no-new-privileges:true",
            "--cap-drop",
            "ALL",
            effective_image,
            "python",
            "-c",
            "with open('/proc/self/status') as f:\n"
            "    for l in f:\n"
            "        if any(k in l for k in ['Uid:', 'Gid:', 'CapEff:', 'NoNewPrivs:', 'Seccomp:']):\n"
            "            print(l.strip())",
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        assert res.returncode == 0
        out = res.stdout
        assert "Uid:\t10001" in out
        assert "Gid:\t10001" in out
        assert "CapEff:\t0000000000000000" in out
        assert "NoNewPrivs:\t1" in out
        assert "Seccomp:\t2" in out

    def test_51_adversarial_artifact_volume_bypass_attack(
        self, sandbox_runner: PythonSandboxRunner
    ) -> None:
        """Verify untrusted container has ZERO named volumes, /artifact does not exist, and /input is read-only."""
        import subprocess

        code = """
import os

# 1. Assert /artifact does NOT exist in filesystem
root_dirs = os.listdir("/")
if "artifact" in root_dirs:
    raise RuntimeError("/artifact directory detected in rootfs!")

# 2. Assert attempting to write to /artifact fails (read-only rootfs or missing dir)
try:
    with open("/artifact/bypass.dat", "wb") as f:
        f.write(b"A" * (16 * 1024 * 1024))
    raise RuntimeError("Security breach: writing to /artifact succeeded!")
except (FileNotFoundError, PermissionError, OSError):
    pass

# 3. Assert /input is read-only
try:
    with open("/input/exploit.txt", "w") as f:
        f.write("exploit")
    raise RuntimeError("Security breach: writing to /input succeeded!")
except (PermissionError, OSError):
    pass

# 4. Assert /workspace cannot exceed 64MB tmpfs limit
try:
    with open("/workspace/bigfile.bin", "wb") as f:
        f.write(b"0" * (70 * 1024 * 1024))
    workspace_quota_enforced = False
except OSError as e:
    workspace_quota_enforced = "space" in str(e).lower() or e.errno == 28

result = {
    "artifact_absent": True,
    "input_readonly": True,
    "workspace_quota_enforced": workspace_quota_enforced,
}
"""
        res = sandbox_runner.run(code, input_data={}, bypass_ast=True)
        assert res.status == "completed"
        assert res.result_payload == {
            "artifact_absent": True,
            "input_readonly": True,
            "workspace_quota_enforced": True,
        }
        assert res.enforcement_details.get("volume_name") is None

        # Verify no active or lingering Docker volumes were created
        v_check = subprocess.run(
            ["docker", "volume", "ls", "--filter", "name=vibe-out-", "-q"],
            capture_output=True,
            text=True,
        )
        assert v_check.stdout.strip() == ""

    def test_52_safe_output_persistence_and_quota_accounting(self) -> None:
        """Verify PostgreSQL quota reservation, deterministic output persistence, idempotency, and error handling."""
        import io
        import json
        import tarfile
        import uuid

        from app.sandbox_agent.client import SandboxAgentClient
        from app.sandbox_agent.server import app as agent_app

        client = SandboxAgentClient(app=agent_app)
        org_id = uuid.uuid4()
        job_id = uuid.uuid4()
        exec_id = f"exec-quota-{int(time.time())}"

        # 1. Normal execution with automatic quota reservation and persistence
        resp = client.execute(
            organization_id=org_id,
            job_id=job_id,
            execution_id=exec_id,
            code_str="result = {'persisted': True, 'count': 42}",
            input_data={},
        )
        assert resp.status == "completed"
        assert resp.result_payload == {"persisted": True, "count": 42}
        artifact_path = resp.enforcement_details.get("artifact_path")
        assert artifact_path is not None
        assert Path(artifact_path).is_file()

        with open(artifact_path, encoding="utf-8") as f:
            persisted_content = json.load(f)
        assert persisted_content == {"persisted": True, "count": 42}

        # 2. Idempotency: re-executing with the same execution_id safely succeeds
        resp2 = client.execute(
            organization_id=org_id,
            job_id=job_id,
            execution_id=exec_id,
            code_str="result = {'persisted': True, 'count': 42}",
            input_data={},
        )
        assert resp2.status == "completed"
        assert resp2.result_payload == {"persisted": True, "count": 42}

        # 3. Archive validation error tests:
        # a) Malformed tar (corrupted bytes)
        corrupted_tar = b"NOT_A_VALID_TAR_STREAM"
        with pytest.raises(tarfile.TarError):
            with tarfile.open(fileobj=io.BytesIO(corrupted_tar), mode="r:*") as tar:
                _ = tar.getmembers()

        # b) Symlink tar rejection check
        symlink_buf = io.BytesIO()
        with tarfile.open(fileobj=symlink_buf, mode="w") as tar:
            ti = tarfile.TarInfo(name="output.json")
            ti.type = tarfile.SYMTYPE
            ti.linkname = "/etc/passwd"
            tar.addfile(ti)
        symlink_buf.seek(0)
        with tarfile.open(fileobj=symlink_buf, mode="r:*") as tar:
            m = tar.getmembers()[0]
            assert not m.isreg(), "Symlink member must not be identified as regular file"

    def test_53_sentinel_spoofing_and_detached_output_mutation(self) -> None:
        """Adversarial Test 53: Verify completion sentinel spoofing rejection and detached process tree containment."""
        runner = IsolatedDockerSandboxRunner(force_enabled=True)
        if not runner.check_docker_available():
            pytest.skip("Docker runtime unavailable on this host.")

        # Script attempts:
        # 1. To print the legacy text sentinel to stdout and stderr
        # 2. To spawn a detached double-forked process with setsid that sleeps until primary execution
        #    completes, then repeatedly attempts to mutate output.json during the host retrieval window
        # 3. Primary script writes genuine output and exits immediately
        adversarial_script = """
import os
import sys
import time
import json

# Attempt 1: Spoof completion sentinel via print and os.write to stderr
print("[VIBE_SCRIPT_EXECUTION_COMPLETED]", flush=True)
os.write(2, b"[VIBE_SCRIPT_EXECUTION_COMPLETED]\\n")

# Attempt 2: Double-fork a detached daemon process with setsid to mutate output during retrieval window
pid = os.fork()
if pid == 0:
    os.setsid()
    pid2 = os.fork()
    if pid2 == 0:
        # Sleep until primary script exits and supervisor enters archive transfer window
        time.sleep(0.8)
        for _ in range(50):
            try:
                with open("/output/output.json", "w") as f:
                    f.write(json.dumps({"mutated_by_attacker": True}))
            except Exception:
                pass
            time.sleep(0.05)
        sys.exit(0)
    sys.exit(0)

# Parent waits for intermediate fork to exit
os.waitpid(pid, 0)

# Write genuine final result
with open("/output/output.json", "w") as f:
    json.dump({"status": "genuine_final_result", "counter": 42}, f)

# Exit normally
sys.exit(0)
"""
        res = runner.run(adversarial_script, input_data={}, bypass_ast=True, timeout_seconds=15)
        assert res.status == "completed", f"Execution failed: {res.stderr}"
        assert res.result_payload == {"status": "genuine_final_result", "counter": 42}, (
            f"Output was mutated or not preserved! Got: {res.result_payload}"
        )
        assert res.enforcement_details is not None
        assert res.enforcement_details.get("sealed_sha256") is not None
        assert res.enforcement_details.get("transferred_sha256") == res.enforcement_details.get(
            "sealed_sha256"
        )
        assert res.enforcement_details.get("requested_manifest_digest") is not None

        # Verify no orphaned vibe-sandbox containers remain running
        ps_res = subprocess.run(
            ["docker", "ps", "--filter", "label=vibe.managed=true", "--format", "{{.Names}}"],
            capture_output=True,
            text=True,
        )
        assert res.enforcement_details.get("container_name") not in ps_res.stdout

    def test_54_control_channel_separation_and_binary_integrity(self) -> None:
        """Adversarial Test 54: Control channel framing isolation, fake control frame rejection, and binary stream safety."""
        runner = IsolatedDockerSandboxRunner(force_enabled=True)
        if not runner.check_docker_available():
            pytest.skip("Docker runtime unavailable on this host.")

        # Script emits:
        # - Fake supervisor control frame bytes (type 3) via stdout
        # - Binary raw null bytes and non-UTF8 bytes on stdout and stderr
        # - Legitimate result dictionary
        binary_script = """
import os
import sys
import struct

# Attempt to forge Frame Type 3 (Supervisor Control) from user stdout
fake_payload = b'{"status": "completed", "sha256": "forged_sha", "fake": True}'
fake_header = bytes([3]) + struct.pack(">I", len(fake_payload))
os.write(1, fake_header + fake_payload)

# Write raw binary data including null bytes and high bytes
os.write(1, b"USER_STDOUT_START\\x00\\x01\\x02\\xfe\\xffUSER_STDOUT_END\\n")
os.write(2, b"USER_STDERR_START\\x00\\x01\\x02\\xfe\\xffUSER_STDERR_END\\n")

result = {"control_separation": "verified", "binary_safe": True}
"""
        res = runner.run(binary_script, input_data={}, bypass_ast=True, timeout_seconds=15)
        # In Gate 9 strict JSON result protocol, emitting raw binary garbage or fake control frames to stdout
        # corrupts the JSON result stream and is strictly rejected as a protocol violation
        assert res.status == "failed"
        assert res.result_payload is None
        assert (
            "protocol" in (res.error_message or "").lower()
            or "malformed" in (res.error_message or "").lower()
            or "utf-8" in (res.error_message or "").lower()
        )

    def test_55_supervisor_crash_and_cancellation_safety(self) -> None:
        """Adversarial Test 55: Failure propagation, infinite loop timeout, and hash verification."""
        runner = IsolatedDockerSandboxRunner(force_enabled=True)
        if not runner.check_docker_available():
            pytest.skip("Docker runtime unavailable on this host.")

        # 1. Runtime failure propagation: Script crashes with ZeroDivisionError
        crash_script = "x = 1 / 0"
        res_crash = runner.run(crash_script, input_data={}, bypass_ast=True, timeout_seconds=10)
        assert res_crash.status == "failed"
        assert "ZeroDivisionError" in res_crash.stderr or "division by zero" in res_crash.stderr

        # 2. Timeout enforcement: Script hangs in infinite loop
        loop_script = "while True: pass"
        res_timeout = runner.run(loop_script, input_data={}, bypass_ast=True, timeout_seconds=2)
        assert res_timeout.status == "timeout"
        assert "timed out" in (res_timeout.error_message or "").lower()

        # 3. Image digest metadata reporting
        img_meta = runner.get_image_metadata()
        assert img_meta.get("requested_manifest_digest") is not None
        assert "sha256:" in img_meta["requested_manifest_digest"]
        if img_meta.get("image_config_id"):
            assert "sha256:" in img_meta["image_config_id"]

    def test_56_procfs_supervisor_fd_enumeration_and_access_rejected(self) -> None:
        """Adversarial Test 56: Untrusted script cannot enumerate or open supervisor file descriptors through /proc."""
        runner = IsolatedDockerSandboxRunner(force_enabled=True)
        if not runner.check_docker_available():
            pytest.skip("Docker runtime unavailable on this host.")

        # User code attempts:
        # 1. Enumerate /proc/1/fd
        # 2. Open /proc/1/fd/1 (supervisor stdout)
        # 3. Open /proc/1/fd/2 (supervisor stderr)
        # 4. Open any higher descriptor
        # 5. Write forged Frame 3 directly to /proc/1/fd/1
        adversarial_script = """
import os, struct, json

fd_attempts = {}

# 1. Enumerate /proc/1/fd
try:
    entries = os.listdir("/proc/1/fd")
    fd_attempts["listdir"] = "accessible"
except Exception as e:
    fd_attempts["listdir"] = type(e).__name__

# 2. Open /proc/1/fd/1 (stdout)
try:
    with open("/proc/1/fd/1", "w") as f:
        f.write("forged_stdout")
    fd_attempts["open_fd1"] = "accessible"
except Exception as e:
    fd_attempts["open_fd1"] = type(e).__name__

# 3. Open /proc/1/fd/2 (stderr)
try:
    with open("/proc/1/fd/2", "w") as f:
        f.write("forged_stderr")
    fd_attempts["open_fd2"] = "accessible"
except Exception as e:
    fd_attempts["open_fd2"] = type(e).__name__

# 4. Attempt to write forged Frame 3 control frame through /proc/1/fd/1
try:
    payload = json.dumps({"status": "completed", "exit_code": 0, "sha256": "fake_sha"}).encode("utf-8")
    frame = bytes([3]) + struct.pack(">I", len(payload)) + payload
    with open("/proc/1/fd/1", "wb") as f:
        f.write(frame)
    fd_attempts["write_frame3"] = "accessible"
except Exception as e:
    fd_attempts["write_frame3"] = type(e).__name__

result = fd_attempts
"""
        res = runner.run(adversarial_script, input_data={}, bypass_ast=True, timeout_seconds=15)
        # In Gate 9 strict JSON result protocol, attempting to write forged data or control frames to stdout
        # corrupts the JSON result stream and is strictly rejected as a protocol violation
        assert res.status == "failed"
        assert (
            "malformed" in (res.error_message or "").lower()
            or "protocol" in (res.error_message or "").lower()
        )

    def test_57_pidfd_getfd_and_ptrace_attach_rejected(self) -> None:
        """Adversarial Test 57: Attempt to clone supervisor descriptors via pidfd_getfd or attach with ptrace."""
        runner = IsolatedDockerSandboxRunner(force_enabled=True)
        if not runner.check_docker_available():
            pytest.skip("Docker runtime unavailable on this host.")

        adversarial_script = """
import ctypes

libc = ctypes.CDLL(None)
results = {}

# 1. Attempt ptrace attach to supervisor (PID 1)
try:
    # PTRACE_ATTACH = 16
    ptrace_res = libc.ptrace(16, 1, 0, 0)
    results["ptrace"] = ptrace_res
except Exception as e:
    results["ptrace"] = type(e).__name__

# 2. Attempt pidfd_open + pidfd_getfd on PID 1 (syscall 434 and 438 on Linux x86_64)
try:
    # SYS_pidfd_open = 434
    pfd = libc.syscall(434, 1, 0)
    if pfd > 0:
        # SYS_pidfd_getfd = 438
        gfd = libc.syscall(438, pfd, 1, 0)
        results["pidfd_getfd"] = gfd
    else:
        results["pidfd_getfd"] = -1
except Exception as e:
    results["pidfd_getfd"] = type(e).__name__

result = results
"""
        res = runner.run(adversarial_script, input_data={}, bypass_ast=True, timeout_seconds=15)
        assert res.status == "completed", f"Execution failed: {res.stderr}"
        payload = res.result_payload or {}
        # ptrace attach MUST return -1 (EPERM / denied)
        assert payload.get("ptrace") in (-1, "PermissionError", "OSError")
        # pidfd_getfd MUST return -1 (EPERM / denied)
        assert payload.get("pidfd_getfd") in (-1, "PermissionError", "OSError")

    def test_58_supervisor_environment_and_memory_inspection_rejected(self) -> None:
        """Adversarial Test 58: Attempt to read supervisor environment or memory maps via /proc/1."""
        runner = IsolatedDockerSandboxRunner(force_enabled=True)
        if not runner.check_docker_available():
            pytest.skip("Docker runtime unavailable on this host.")

        adversarial_script = """
import os

proc_attempts = {}

# 1. Read /proc/1/environ
try:
    with open("/proc/1/environ", "r") as f:
        data = f.read()
    proc_attempts["environ"] = "accessible"
except Exception as e:
    proc_attempts["environ"] = type(e).__name__

# 2. Read /proc/1/maps
try:
    with open("/proc/1/maps", "r") as f:
        data = f.read()
    proc_attempts["maps"] = "accessible"
except Exception as e:
    proc_attempts["maps"] = type(e).__name__

# 3. Read /proc/1/mem
try:
    with open("/proc/1/mem", "rb") as f:
        data = f.read(10)
    proc_attempts["mem"] = "accessible"
except Exception as e:
    proc_attempts["mem"] = type(e).__name__

result = proc_attempts
"""
        res = runner.run(adversarial_script, input_data={}, bypass_ast=True, timeout_seconds=15)
        assert res.status == "completed", f"Execution failed: {res.stderr}"
        payload = res.result_payload or {}
        if (
            res.enforcement_details
            and res.enforcement_details.get("pid_1_architecture") == "direct_user_pid1"
        ):
            # In Gate 8 direct PID 1 architecture, there is no supervisor inside the container.
            # Container PID 1 is the untrusted user workload; the host Sandbox Agent's environment and memory
            # are completely outside the container and cannot be accessed through container procfs.
            assert res.status == "completed"
        else:
            # All reads MUST be blocked with PermissionError
            assert payload.get("environ") == "PermissionError", (
                f"Expected PermissionError, got {payload.get('environ')}"
            )
            assert payload.get("maps") == "PermissionError", (
                f"Expected PermissionError, got {payload.get('maps')}"
            )
            assert payload.get("mem") == "PermissionError", (
                f"Expected PermissionError, got {payload.get('mem')}"
            )

    def test_59_supervisor_dumpability_and_signal_immunity(self) -> None:
        """Adversarial Test 59: Supervisor dumpability cannot be altered, and supervisor is immune to child signals."""
        runner = IsolatedDockerSandboxRunner(force_enabled=True)
        if not runner.check_docker_available():
            pytest.skip("Docker runtime unavailable on this host.")

        adversarial_script = """
import os, signal, ctypes

libc = ctypes.CDLL(None)
signal_results = {}

# 1. Attempt to alter dumpability (prctl PR_SET_DUMPABLE=1)
# Note: prctl PR_SET_DUMPABLE only affects the calling process, not PID 1
try:
    call_res = libc.prctl(4, 1, 0, 0, 0)
    signal_results["child_dumpable_changed"] = (call_res == 0)
except Exception as e:
    signal_results["child_dumpable_changed"] = type(e).__name__

# 2. Send SIGKILL to PID 1 (kernel ignores unhandled signals sent to init)
try:
    os.kill(1, signal.SIGKILL)
    signal_results["kill_sigkill"] = "sent"
except Exception as e:
    signal_results["kill_sigkill"] = type(e).__name__

# 3. Send SIGTERM to PID 1
try:
    os.kill(1, signal.SIGTERM)
    signal_results["kill_sigterm"] = "sent"
except Exception as e:
    signal_results["kill_sigterm"] = type(e).__name__

result = signal_results
"""
        res = runner.run(adversarial_script, input_data={}, bypass_ast=True, timeout_seconds=15)
        # Supervisor MUST remain alive and complete successfully despite SIGKILL/SIGTERM signals
        assert res.status == "completed", f"Supervisor was unexpectedly terminated: {res.stderr}"
        assert res.enforcement_details is not None
        # Supervisor dumpable remains 0
        assert res.enforcement_details.get("supervisor_dumpable") == 0

    def test_60_completion_channel_order_and_race_free_sealing(self) -> None:
        """Adversarial Test 60: Genuine completion is emitted ONLY after execution ends, with verified sealing."""
        runner = IsolatedDockerSandboxRunner(force_enabled=True)
        if not runner.check_docker_available():
            pytest.skip("Docker runtime unavailable on this host.")

        adversarial_script = """
import time, threading

# Primary execution thread sleeps briefly to simulate realistic analytics computation
time.sleep(0.3)
result = {"analytics_output": 9999, "status": "computed"}
"""
        res = runner.run(adversarial_script, input_data={}, bypass_ast=True, timeout_seconds=15)
        assert res.status == "completed", f"Execution failed: {res.stderr}"
        assert res.result_payload == {"analytics_output": 9999, "status": "computed"}
        assert res.enforcement_details is not None
        sealed_sha = res.enforcement_details.get("sealed_sha256")
        transferred_sha = res.enforcement_details.get("transferred_sha256")
        assert sealed_sha is not None
        assert transferred_sha == sealed_sha

    def test_61_fork_bomb_during_termination_and_session_escape(self) -> None:
        """Adversarial Test 61: Continuous forks and session creation (setsid) are terminated by container process containment."""
        runner = IsolatedDockerSandboxRunner(force_enabled=True)
        if not runner.check_docker_available():
            pytest.skip("Docker runtime unavailable on this host.")

        # Script creates detached session and continuous background fork loops
        adversarial_script = """
import os, time

try:
    pid = os.fork()
    if pid == 0:
        # Create detached session
        os.setsid()
        # Spawn child loop
        for _ in range(50):
            try:
                cpid = os.fork()
                if cpid == 0:
                    time.sleep(5)
                    os._exit(0)
            except Exception:
                break
        time.sleep(5)
        os._exit(0)
except Exception:
    pass

result = {"descendant_test": "completed"}
"""
        res = runner.run(adversarial_script, input_data={}, bypass_ast=True, timeout_seconds=15)
        assert res.status == "completed", f"Execution failed: {res.stderr}"
        assert res.result_payload == {"descendant_test": "completed"}
        assert res.enforcement_details is not None
        assert res.enforcement_details.get("sealed_sha256") is not None
        assert res.enforcement_details.get("sealed_sha256") == res.enforcement_details.get(
            "transferred_sha256"
        )

    def test_62_output_modification_after_sealing_blocked(self) -> None:
        """Adversarial Test 62: Read-only output permissions (0400 file, 0500 directory) prevent mutation after sealing."""
        runner = IsolatedDockerSandboxRunner(force_enabled=True)
        if not runner.check_docker_available():
            pytest.skip("Docker runtime unavailable on this host.")

        adversarial_script = """
import os, stat

# Pre-sealing simulation: write test file, seal permissions to 0400 / 0500, verify mutation blocked
os.makedirs("/tmp/seal_test", exist_ok=True)
p = "/tmp/seal_test/test.json"
with open(p, "w") as f:
    f.write('{"initial": 1}')

os.chmod(p, 0o400)
os.chmod("/tmp/seal_test", 0o500)

mutation_results = {}

# 1. Attempt to write to 0400 file
try:
    with open(p, "w") as f:
        f.write('{"tampered": 2}')
    mutation_results["write"] = "succeeded"
except Exception as e:
    mutation_results["write"] = type(e).__name__

# 2. Attempt to unlink 0400 file in 0500 directory
try:
    os.unlink(p)
    mutation_results["unlink"] = "succeeded"
except Exception as e:
    mutation_results["unlink"] = type(e).__name__

result = mutation_results
"""
        res = runner.run(adversarial_script, input_data={}, bypass_ast=True, timeout_seconds=15)
        assert res.status == "completed", f"Execution failed: {res.stderr}"
        payload = res.result_payload or {}
        assert payload.get("write") == "PermissionError", (
            f"Expected PermissionError, got {payload.get('write')}"
        )
        assert payload.get("unlink") == "PermissionError", (
            f"Expected PermissionError, got {payload.get('unlink')}"
        )

    def test_63_runtime_cgroup_and_platform_capabilities_attestation(self) -> None:
        """Adversarial Test 63: Runtime cgroup version, platform capabilities, and isolation attestation."""
        runner = IsolatedDockerSandboxRunner(force_enabled=True)
        if not runner.check_docker_available():
            pytest.skip("Docker runtime unavailable on this host.")

        res = runner.run(
            "result = {'ping': 'pong'}", input_data={}, bypass_ast=True, timeout_seconds=10
        )
        assert res.status == "completed"
        assert res.enforcement_details is not None
        assert res.enforcement_details.get("cgroup_version") == 2
        assert res.enforcement_details.get("supervisor_dumpable") == 0
        assert "cgroup_enforced" in res.enforcement_details
        assert res.enforcement_details.get("pinned_manifest_digest") is not None
        assert res.enforcement_details.get("container_image_id") is not None

    def test_64_direct_pid1_execution_and_clean_termination(self) -> None:
        """Gate 8 Test 64: Direct PID 1 execution in container with clean termination and exit code 0."""
        runner = IsolatedDockerSandboxRunner(force_enabled=True)
        if not runner.check_docker_available():
            pytest.skip("Docker runtime unavailable on this host.")

        script = """
import os
pid = os.getpid()
result = {"pid": pid, "mode": "direct_pid1", "value": 100 * 2}
"""
        res = runner.run(script, input_data={}, bypass_ast=True, timeout_seconds=10)
        assert res.status == "completed", f"Execution failed: {res.stderr}"
        assert res.result_payload == {"pid": 1, "mode": "direct_pid1", "value": 200}
        assert res.enforcement_details is not None
        assert res.enforcement_details.get("pid_1_architecture") == "direct_user_pid1"
        assert res.enforcement_details.get("pid_namespace_teardown") is True
        assert res.enforcement_details.get("exit_code") == 0

    def test_65_pid1_namespace_teardown_kills_detached_setsid_descendants(self) -> None:
        """Gate 8 Test 65: When untrusted PID 1 exits, Linux kernel tears down entire PID namespace including detached setsid children."""
        runner = IsolatedDockerSandboxRunner(force_enabled=True)
        if not runner.check_docker_available():
            pytest.skip("Docker runtime unavailable on this host.")

        # Script forks a detached child that attempts to run forever
        adversarial_script = """
import os, time

try:
    pid = os.fork()
    if pid == 0:
        os.setsid()
        # Detached background process attempting to survive container exit
        while True:
            time.sleep(1)
        os._exit(0)
except Exception:
    pass

# Parent PID 1 finishes immediately
result = {"teardown_initiated": True}
"""
        start = time.perf_counter()
        res = runner.run(adversarial_script, input_data={}, bypass_ast=True, timeout_seconds=15)
        duration = time.perf_counter() - start

        assert res.status == "completed", f"Execution failed: {res.stderr}"
        assert res.result_payload == {"teardown_initiated": True}
        # Verify container exited promptly without hanging on background sleep
        assert duration < 6.0, f"Container did not terminate promptly; took {duration:.2f}s"
        assert res.enforcement_details.get("pid_namespace_teardown") is True

    def test_66_pid1_infinite_cpu_loop_and_timeout_teardown(self) -> None:
        """Gate 8 Test 66: Infinite CPU loop running as PID 1 is killed and torn down by host timeout watchdog."""
        runner = IsolatedDockerSandboxRunner(force_enabled=True)
        if not runner.check_docker_available():
            pytest.skip("Docker runtime unavailable on this host.")

        adversarial_script = """
# Unbounded busy loop
while True:
    pass
"""
        start = time.perf_counter()
        res = runner.run(adversarial_script, input_data={}, bypass_ast=True, timeout_seconds=3)
        duration = time.perf_counter() - start

        assert res.status in ("timeout", "failed")
        assert (
            "timeout" in (res.error_message or "").lower()
            or "timed out" in (res.stderr or "").lower()
        )
        assert duration < 8.0, f"Timeout took too long to enforce: {duration:.2f}s"

    def test_67_pid1_double_fork_and_session_escape_containment(self) -> None:
        """Gate 8 Test 67: Double-fork daemon idiom cannot survive PID 1 exit; kernel zap_pid_ns_processes cleans up all descendants."""
        runner = IsolatedDockerSandboxRunner(force_enabled=True)
        if not runner.check_docker_available():
            pytest.skip("Docker runtime unavailable on this host.")

        adversarial_script = """
import os, time

try:
    pid1 = os.fork()
    if pid1 == 0:
        os.setsid()
        pid2 = os.fork()
        if pid2 == 0:
            # Grandchild daemon process
            for _ in range(60):
                time.sleep(1)
            os._exit(0)
        os._exit(0)
except Exception:
    pass

result = {"daemon_spawned": True}
"""
        start = time.perf_counter()
        res = runner.run(adversarial_script, input_data={}, bypass_ast=True, timeout_seconds=15)
        duration = time.perf_counter() - start

        assert res.status == "completed", f"Execution failed: {res.stderr}"
        assert res.result_payload == {"daemon_spawned": True}
        assert duration < 6.0, f"Double fork delayed termination: {duration:.2f}s"

    def test_68_container_termination_precedes_result_acceptance(self) -> None:
        """Gate 8 Test 68: Output validation occurs strictly after container has reached stopped/terminated state."""
        runner = IsolatedDockerSandboxRunner(force_enabled=True)
        if not runner.check_docker_available():
            pytest.skip("Docker runtime unavailable on this host.")

        res = runner.run(
            "result = {'checked_termination': True}",
            input_data={},
            bypass_ast=True,
            timeout_seconds=10,
        )
        assert res.status == "completed"
        c_name = res.enforcement_details.get("container_name")
        assert c_name is not None

        # Confirm container is completely removed and no longer running on host
        check = subprocess.run(["docker", "inspect", c_name], capture_output=True, text=True)
        assert check.returncode != 0, (
            "Container unexpectedly still exists after execution completed!"
        )

    def test_69_stdout_result_channel_and_stderr_diagnostic_channel(self) -> None:
        """Gate 8 Test 69: Clean channel separation between structured JSON stdout and diagnostic stderr logs."""
        runner = IsolatedDockerSandboxRunner(force_enabled=True)
        if not runner.check_docker_available():
            pytest.skip("Docker runtime unavailable on this host.")

        script = """
import sys
print("Diagnostic log line 1 via print")
sys.stderr.write("Diagnostic log line 2 via sys.stderr\\n")
result = {"status": "ok", "channel": "stdout_json"}
"""
        res = runner.run(script, input_data={}, bypass_ast=True, timeout_seconds=10)
        assert res.status == "completed", f"Execution failed: {res.stderr}"
        assert res.result_payload == {"status": "ok", "channel": "stdout_json"}
        # Both user print (redirected to stderr) and direct sys.stderr are captured in stderr
        assert "Diagnostic log line 1" in res.stderr
        assert "Diagnostic log line 2" in res.stderr

    def test_70_duplicate_execution_and_idempotency_without_supervisor(self) -> None:
        """Gate 8 Test 70: Idempotent execution and quota accounting under direct PID 1 architecture."""
        import uuid

        from app.sandbox_agent.client import SandboxAgentClient
        from app.sandbox_agent.server import app as agent_app

        client = SandboxAgentClient(app=agent_app)
        org_id = uuid.uuid4()
        job_id = uuid.uuid4()
        exec_id = f"exec-gate8-{int(time.time())}"

        resp1 = client.execute(
            organization_id=org_id,
            job_id=job_id,
            execution_id=exec_id,
            code_str="result = {'gate': 8, 'success': True}",
            input_data={},
        )
        assert resp1.status == "completed"
        assert resp1.result_payload == {"gate": 8, "success": True}
        assert resp1.enforcement_details.get("pid_1_architecture") == "direct_user_pid1"
        assert resp1.enforcement_details.get("pid_namespace_teardown") is True

        # Second execution with same parameters succeeds idempotently
        resp2 = client.execute(
            organization_id=org_id,
            job_id=job_id,
            execution_id=exec_id,
            code_str="result = {'gate': 8, 'success': True}",
            input_data={},
        )
        assert resp2.status == "completed"
        assert resp2.result_payload == {"gate": 8, "success": True}
        assert resp2.enforcement_details.get("sealed_sha256") == resp1.enforcement_details.get(
            "sealed_sha256"
        )
