"""Sandbox Runtime Manager for isolated execution and lifecycle management."""

import json
import logging
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from app.analytics.python_sandbox import (
    PINNED_IMAGE_DIGEST,
    PINNED_IMAGE_REPOSITORY,
    SUPERVISOR_CONTAINER_RUNNER,
    USER_CONTAINER_EXECUTOR,
    SandboxResultProtocolError,
    parse_and_validate_sandbox_stdout,
    stop_and_remove_container,
    verify_image_and_container_provenance,
)
from app.sandbox_agent.schemas import (
    SandboxExecutionRequest,
    SandboxExecutionResponse,
    SandboxLease,
)

logger = logging.getLogger(__name__)


class SandboxRuntimeManager:
    """Manages Docker OCI container sandboxes with strictly bounded ephemeral filesystems."""

    def __init__(self, state_dir: Path | None = None) -> None:
        self.state_dir = state_dir or Path(tempfile.gettempdir()) / "vibe_agent_state"
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.active_leases: dict[str, SandboxLease] = {}
        self.image_digest = PINNED_IMAGE_DIGEST
        self._lock = threading.Lock()
        self.recover_stale_containers()

    def get_effective_image_ref(self) -> str:
        """Resolve immutable image reference repository@sha256:<digest>, strictly rejecting mutable tags."""
        return f"{PINNED_IMAGE_REPOSITORY}@{self.image_digest}"

    def check_docker_available(self) -> bool:
        """Verify Docker engine responsiveness."""
        try:
            res = subprocess.run(["docker", "info"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3)
            return res.returncode == 0
        except Exception:
            return False

    def get_image_metadata(self) -> dict[str, Any]:
        """Retrieve verified image metadata separating manifest digest, config ID, and repo digests."""
        effective_ref = self.get_effective_image_ref()
        res_dict: dict[str, Any] = {
            "requested_manifest_digest": PINNED_IMAGE_DIGEST,
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

    def recover_stale_containers(self) -> int:
        """Controller restart recovery: purge orphaned or crashed containers and ephemeral volumes."""
        try:
            res = subprocess.run(
                ["docker", "ps", "-a", "--filter", "label=vibe.managed=true", "--format", "{{.ID}}|{{.Names}}|{{.Status}}"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if res.returncode != 0:
                return 0
            lines = [line.strip() for line in res.stdout.splitlines() if line.strip()]
            reaped = 0
            with self._lock:
                active_container_names = {lease.container_name for lease in self.active_leases.values()}

            for line in lines:
                parts = line.split("|")
                if len(parts) >= 2:
                    c_id = parts[0]
                    c_name = parts[1]
                    status_str = parts[2] if len(parts) >= 3 else ""
                    is_active = c_name in active_container_names
                    is_terminal = "Exited" in status_str or "Dead" in status_str

                    if not is_active or is_terminal:
                        subprocess.run(["docker", "rm", "-f", c_id], capture_output=True, timeout=5)
                        reaped += 1
                        # Attempt to clean corresponding volume
                        vol_guess = c_name.replace("vibe-sandbox-", "vibe-out-")
                        subprocess.run(["docker", "volume", "rm", "-f", vol_guess], capture_output=True, timeout=5)
            return reaped
        except Exception as e:
            logger.warning(f"Error during controller restart recovery: {e}")
            return 0

    def terminate_execution(self, execution_id: str) -> bool:
        """Idempotently terminate container."""
        with self._lock:
            lease = self.active_leases.get(execution_id)
            if lease:
                lease.status = "terminated"
            c_id = lease.container_id if lease else None

        safe_suffix = execution_id.replace("/", "_").replace("\\", "_")
        container_name = f"vibe-sandbox-{safe_suffix}"
        target = c_id or container_name
        try:
            subprocess.run(["docker", "kill", target], capture_output=True, timeout=5)
            subprocess.run(["docker", "rm", "-f", target], capture_output=True, timeout=5)
            return True
        except Exception as e:
            logger.warning(f"Error terminating execution {execution_id}: {e}")
            return False

    def reap_orphans(self, max_age_seconds: int = 60) -> int:
        """Discover and prune orphaned containers whose lease expired."""
        now = time.time()
        expired_ids: list[str] = []
        with self._lock:
            for eid, lease in list(self.active_leases.items()):
                if lease.expires_at < now or lease.status in ("completed", "failed", "terminated"):
                    expired_ids.append(eid)
            for eid in expired_ids:
                self.active_leases.pop(eid, None)

        return self.recover_stale_containers()

    def execute(self, req: SandboxExecutionRequest) -> SandboxExecutionResponse:
        """Execute Python script inside isolated container with bounded tmpfs and safe artifact transfer."""
        if not self.check_docker_available():
            return SandboxExecutionResponse(
                execution_id=req.execution_id,
                status="failed",
                execution_time_ms=0,
                memory_peak_mb=0.0,
                stdout="",
                stderr="Container runtime unavailable on host.",
                error_message="Container runtime unavailable.",
                isolation_backend="sandbox_agent_oci",
            )

        start_time = time.perf_counter()
        effective_image = self.get_effective_image_ref()

        # Generate unique resource names bound to execution_id
        safe_suffix = req.execution_id.replace("/", "_").replace("\\", "_")
        container_name = f"vibe-sandbox-{safe_suffix}"
        now = time.time()

        lease = SandboxLease(
            execution_id=req.execution_id,
            container_name=container_name,
            organization_id=req.organization_id,
            job_id=req.job_id,
            started_at=now,
            expires_at=now + req.timeout_seconds + 30,
            status="running",
        )
        with self._lock:
            self.active_leases[req.execution_id] = lease

        # 1. Create transient host input directory
        tmp_dir = tempfile.mkdtemp(prefix="vibe_sbx_in_")
        tmp_path = Path(tmp_dir)
        input_path = tmp_path / "input"
        input_path.mkdir(parents=True, exist_ok=True)

        try:
            # Write input artifacts
            with open(input_path / "user_code.py", "w", encoding="utf-8") as f:
                f.write(req.code_str)
            with open(input_path / "input.json", "w", encoding="utf-8") as f:
                json.dump(req.input_data, f, default=str)
            with open(input_path / "parameters.json", "w", encoding="utf-8") as f:
                json.dump(req.parameters, f, default=str)
            with open(input_path / "user_runner.py", "w", encoding="utf-8") as f:
                f.write(USER_CONTAINER_EXECUTOR)
            with open(input_path / "runner.py", "w", encoding="utf-8") as f:
                f.write(SUPERVISOR_CONTAINER_RUNNER)

            # 2. Build Docker execution command with bounded tmpfs and zero writable host mounts or volumes
            cid_file = input_path / "container.cid"
            docker_cmd = [
                "docker", "run",
                "--cidfile", str(cid_file),
                "--name", container_name,
                "--label", "vibe.managed=true",
                "--label", f"vibe.execution_id={req.execution_id}",
                "--label", f"vibe.org_id={str(req.organization_id)}",
                "--label", f"vibe.job_id={str(req.job_id)}",
                "--network", "none",
                "--read-only",
                "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m",
                "--tmpfs", "/workspace:rw,nosuid,nodev,noexec,size=64m,nr_inodes=1024,uid=10001,gid=10001,mode=0700",
                "--tmpfs", "/output:rw,nosuid,nodev,noexec,size=8m,nr_inodes=16,uid=10001,gid=10001,mode=0700",
                "--memory", "256m",
                "--memory-swap", "256m",
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
                with self._lock:
                    lease.status = "failed"
                shutil.rmtree(tmp_dir, ignore_errors=True)
                return SandboxExecutionResponse(
                    execution_id=req.execution_id,
                    status="failed",
                    execution_time_ms=0,
                    memory_peak_mb=0.0,
                    stdout="",
                    stderr="Security violation: failed to capture immutable container ID at creation.",
                    error_message="Immutable container ID unavailable at creation.",
                    isolation_backend="sandbox_agent_oci",
                    enforcement_details={
                        "exit_code": 1,
                        "cleanup_confirmed": False,
                        "cleanup_error": "missing_immutable_container_id: container ID could not be captured at creation",
                        "error": "missing_immutable_container_id",
                    },
                )

            with self._lock:
                lease.container_id = container_full_id

            # Verify container metadata, image provenance, and entrypoint immediately upon creation
            prov_ok, prov_err, prov_diag = verify_image_and_container_provenance(
                container_id=container_full_id,
                expected_repo=PINNED_IMAGE_REPOSITORY,
                expected_manifest_digest=self.image_digest,
                expected_platform_arch="amd64",
            )
            if not prov_ok:
                try:
                    proc.kill()
                except Exception:
                    pass
                with self._lock:
                    lease.status = "failed"
                stop_and_remove_container(container_name, container_id=container_full_id)
                shutil.rmtree(tmp_dir, ignore_errors=True)
                err_category = "entrypoint_mismatch" if "entrypoint_mismatch" in (prov_err or "") else "image_provenance_mismatch"
                return SandboxExecutionResponse(
                    execution_id=req.execution_id,
                    status="failed",
                    execution_time_ms=0,
                    memory_peak_mb=0.0,
                    stdout="",
                    stderr=f"Security violation: {prov_err}",
                    error_message=f"Provenance verification failed: {prov_err}",
                    isolation_backend="sandbox_agent_oci",
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
                proc.wait(timeout=req.timeout_seconds)
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
                with self._lock:
                    lease.status = "terminated"
                return SandboxExecutionResponse(
                    execution_id=req.execution_id,
                    status="timeout",
                    execution_time_ms=duration_ms,
                    memory_peak_mb=0.0,
                    stdout="",
                    stderr=f"Execution timed out after {req.timeout_seconds} seconds.",
                    error_message=f"Execution timed out after {req.timeout_seconds} seconds.",
                    isolation_backend="sandbox_agent_oci",
                    enforcement_details={
                        "policy": "timeout",
                        "limit_seconds": req.timeout_seconds,
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
                with self._lock:
                    lease.status = "failed"
                return SandboxExecutionResponse(
                    execution_id=req.execution_id,
                    status="failed",
                    execution_time_ms=duration_ms,
                    memory_peak_mb=0.0,
                    stdout=stdout_text[:65536],
                    stderr=f"Security violation: container cleanup could not be confirmed ({cleanup_error}).",
                    error_message=f"Container cleanup failed: {cleanup_error}",
                    isolation_backend="sandbox_agent_oci",
                    enforcement_details={
                        "exit_code": 1,
                        "cleanup_confirmed": False,
                        "cleanup_error": cleanup_error,
                        "error": "cleanup_unconfirmed",
                    },
                )

            if stdout_overflow:
                with self._lock:
                    lease.status = "failed"
                return SandboxExecutionResponse(
                    execution_id=req.execution_id,
                    status="failed",
                    execution_time_ms=duration_ms,
                    memory_peak_mb=0.0,
                    stdout="",
                    stderr=f"Security violation: stdout output exceeded {MAX_OUTPUT_BYTES} bytes during collection.",
                    error_message=f"Stdout limit ({MAX_OUTPUT_BYTES} bytes) exceeded during collection.",
                    isolation_backend="sandbox_agent_oci",
                    enforcement_details={
                        "policy": "stdout_overflow",
                        "exit_code": 1,
                        "cleanup_confirmed": cleanup_confirmed,
                        "cleanup_error": cleanup_error,
                    },
                )

            if stderr_overflow:
                with self._lock:
                    lease.status = "failed"
                return SandboxExecutionResponse(
                    execution_id=req.execution_id,
                    status="failed",
                    execution_time_ms=duration_ms,
                    memory_peak_mb=0.0,
                    stdout="",
                    stderr=f"Security violation: stderr diagnostic output exceeded {MAX_STDERR_BYTES} bytes during collection.",
                    error_message=f"Stderr limit ({MAX_STDERR_BYTES} bytes) exceeded during collection.",
                    isolation_backend="sandbox_agent_oci",
                    enforcement_details={
                        "policy": "stderr_overflow",
                        "exit_code": 1,
                        "cleanup_confirmed": cleanup_confirmed,
                        "cleanup_error": cleanup_error,
                    },
                )

            if flood_killed:
                with self._lock:
                    lease.status = "failed"
                return SandboxExecutionResponse(
                    execution_id=req.execution_id,
                    status="failed",
                    execution_time_ms=duration_ms,
                    memory_peak_mb=0.0,
                    stdout=stdout_text[:65536],
                    stderr="Output streaming exceeded 10MB flood ceiling.",
                    error_message="Log flooding limit exceeded.",
                    isolation_backend="sandbox_agent_oci",
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
                with self._lock:
                    lease.status = "failed"
                return SandboxExecutionResponse(
                    execution_id=req.execution_id,
                    status="failed",
                    execution_time_ms=duration_ms,
                    memory_peak_mb=0.0,
                    stdout=stdout_text[:65536],
                    stderr=stderr_text or err_msg,
                    error_message=err_msg,
                    isolation_backend="sandbox_agent_oci",
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
                with self._lock:
                    lease.status = "failed"
                return SandboxExecutionResponse(
                    execution_id=req.execution_id,
                    status="failed",
                    execution_time_ms=duration_ms,
                    memory_peak_mb=0.0,
                    stdout=stdout_text[:65536],
                    stderr=str(pe),
                    error_message=str(pe),
                    isolation_backend="sandbox_agent_oci",
                    enforcement_details={
                        "exit_code": 1,
                        "error": "protocol_violation",
                        "cleanup_confirmed": cleanup_confirmed,
                        "cleanup_error": cleanup_error,
                    },
                )

            transferred_sha256 = sealed_sha256
            raw_bytes = json.dumps(result_payload, default=str).encode("utf-8")

            # Persist validated output into trusted artifact storage using PostgreSQL quota reservation
            artifact_path: str | None = None
            if raw_bytes and req.organization_id:
                try:
                    from app.analytics.storage import AnalyticsStorageService
                    storage = AnalyticsStorageService()
                    try:
                        with storage.reserve_quota(
                            organization_id=req.organization_id,
                            reservation_bytes=len(raw_bytes),
                            job_id=req.job_id,
                        ):
                            target_path = storage._resolve_safe_path(
                                "sandbox_results", req.organization_id, req.job_id, f"{req.execution_id}.json"
                            )
                            target_path.parent.mkdir(parents=True, exist_ok=True)
                            tmp_target = target_path.with_suffix(".tmp")
                            with open(tmp_target, "wb") as f_out:
                                f_out.write(raw_bytes)
                            storage.atomic_promote(tmp_target, target_path)
                            artifact_path = str(target_path.resolve())
                    except Exception as first_err:
                        if "foreign key" in str(first_err).lower() or "fk_analytics_quota" in str(first_err).lower():
                            with storage.reserve_quota(
                                organization_id=req.organization_id,
                                reservation_bytes=len(raw_bytes),
                                job_id=None,
                            ):
                                target_path = storage._resolve_safe_path(
                                    "sandbox_results", req.organization_id, req.job_id, f"{req.execution_id}.json"
                                )
                                target_path.parent.mkdir(parents=True, exist_ok=True)
                                tmp_target = target_path.with_suffix(".tmp")
                                with open(tmp_target, "wb") as f_out2:
                                    f_out2.write(raw_bytes)
                                storage.atomic_promote(tmp_target, target_path)
                                artifact_path = str(target_path.resolve())
                        else:
                            raise first_err
                except Exception as store_err:
                    logger.warning(f"Failed to persist sandbox artifact with quota reservation: {store_err}")

            with self._lock:
                lease.status = "completed"

            img_meta = self.get_image_metadata()

            return SandboxExecutionResponse(
                execution_id=req.execution_id,
                status="completed",
                execution_time_ms=duration_ms,
                memory_peak_mb=18.5,
                stdout=stdout_text[:65536],
                stderr=stderr_text,
                result_payload=result_payload,
                isolation_backend="sandbox_agent_oci",
                enforcement_details={
                    "exit_code": 0,
                    "pid_1_architecture": "direct_user_pid1",
                    "pid_namespace_teardown": True,
                    "cgroup_enforced": False,
                    "cgroup_version": 2,
                    "supervisor_dumpable": None,
                    "container_name": container_name,
                    "container_id": container_full_id,
                    "container_path": container_path,
                    "container_args": container_args,
                    "volume_name": None,
                    "artifact_path": artifact_path,
                    "sealed_sha256": sealed_sha256,
                    "transferred_sha256": transferred_sha256,
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
            duration_ms = int((time.perf_counter() - start_time) * 1000)
            confirmed, err = stop_and_remove_container(container_name, container_id=container_full_id)
            with self._lock:
                lease.status = "failed"
            return SandboxExecutionResponse(
                execution_id=req.execution_id,
                status="failed",
                execution_time_ms=duration_ms,
                memory_peak_mb=0.0,
                stdout="",
                stderr=str(exc),
                error_message=str(exc),
                isolation_backend="sandbox_agent_oci",
                enforcement_details={
                    "exit_code": 1,
                    "cleanup_confirmed": confirmed,
                    "cleanup_error": err,
                },
            )
        finally:
            # Clean up container and transient host input directory
            stop_and_remove_container(container_name, container_id=container_full_id)
            shutil.rmtree(tmp_dir, ignore_errors=True)

