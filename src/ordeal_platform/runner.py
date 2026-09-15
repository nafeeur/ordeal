from __future__ import annotations

import json
import math
import os
from pathlib import Path
import re
import selectors
import signal
import subprocess
import sys
import threading
import time
import uuid

from ordeal_agent.telemetry import PlatformClient

from . import db, schemas, services
from .operations import evaluate_trace, system_identity


class ExecutionFailure(RuntimeError):
    pass


class RunnerPolicy:
    def __init__(self, workspace: str | Path, allowed_paths: list[str], *, mode: str,
                 image: str = "", timeout: float = 300, memory_mb: int = 512, cpus: float = 1,
                 max_output_bytes: int = 2 * 1024 * 1024):
        self.workspace = Path(workspace).resolve()
        self.allowed_paths = {str(Path(p).as_posix()) for p in allowed_paths}
        if not self.allowed_paths:
            raise ValueError("Explicit --allow-suite paths are required; the server cannot choose arbitrary runner code")
        if mode not in {"docker", "trusted-process"}:
            raise ValueError("Choose docker isolation or explicitly opt into trusted-process execution")
        if mode == "docker" and not re.fullmatch(r"(?:[A-Za-z0-9_./:-]+@)?sha256:[a-f0-9]{64}", image):
            raise ValueError("Docker execution requires a digest-pinned image or local sha256 image id")
        if not math.isfinite(timeout) or not math.isfinite(cpus) or timeout <= 0 or timeout > 86400 or memory_mb < 64 or cpus <= 0 or max_output_bytes < 1024:
            raise ValueError("Invalid runner resource limits")
        self.mode, self.image = mode, image
        self.timeout, self.memory_mb, self.cpus, self.max_output_bytes = timeout, memory_mb, cpus, max_output_bytes

    def resolve(self, path: str) -> Path:
        if path not in self.allowed_paths:
            raise ExecutionFailure("Suite/evaluator path is not allowlisted on this runner")
        target = (self.workspace / path).resolve()
        if not target.is_file() or not target.is_relative_to(self.workspace) or target.suffix != ".py":
            raise ExecutionFailure("Execution path escapes the runner workspace or is not a Python file")
        return target

    def command(self, path: str, *, evaluator=False, extra_env=None):
        self.resolve(path)
        if os.name != "posix":
            raise ExecutionFailure("This runner release requires a POSIX host; server and SDK remain portable")
        extra_env = extra_env or {}
        if self.mode == "trusted-process":
            # Deliberately not a sandbox. Only explicitly allowlisted, operator-owned code belongs here.
            argv = [sys.executable, "-m", "ordeal_platform.suite_exec", "--root", str(self.workspace),
                    "--evaluator" if evaluator else "--suite", path]
            return argv, None
        name = "ordeal-" + uuid.uuid4().hex
        if "," in str(self.workspace):
            raise ExecutionFailure("Docker mount paths containing commas are not supported")
        argv = ["docker", "run", "--rm", "--name", name, "--network", "none", "--read-only",
                "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true", "--pids-limit", "128",
                "--memory", f"{self.memory_mb}m", "--memory-swap", f"{self.memory_mb}m", "--cpus", str(self.cpus),
                "--user", "65532:65532", "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m",  # nosec B108 - docker --tmpfs mount destination, not a host temp-file path
                "--mount", f"type=bind,src={self.workspace},dst=/workspace,readonly",
                "-e", "PYTHONDONTWRITEBYTECODE=1", "-i"]
        for key in extra_env:
            argv.extend(["-e", key])  # Value comes from the child environment, never the process command line.
        argv += [self.image, "python", "-m", "ordeal_platform.suite_exec", "--root", "/workspace",
                 "--evaluator" if evaluator else "--suite", path]
        return argv, name


def terminate(proc):
    if proc.poll() is not None:
        return
    try:
        os.killpg(proc.pid, signal.SIGTERM)
        proc.wait(timeout=.5)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.wait(timeout=5)


def bounded_execute(argv: list[str], *, env: dict, cwd: Path, timeout: float, max_output: int,
                    cancel: threading.Event | None = None, input_data: bytes = b"") -> tuple[int, bytes, bytes]:
    proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            env=env, cwd=cwd, start_new_session=True)
    selector = selectors.DefaultSelector()
    streams = {"stdout": bytearray(), "stderr": bytearray()}
    deadline = time.monotonic() + timeout
    try:
        # Input is also bounded so an evaluator cannot cause unbounded IPC memory use.
        if len(input_data) > max_output:
            raise ExecutionFailure("Execution input exceeds the runner size limit")
        for name, stream in (("stdout", proc.stdout), ("stderr", proc.stderr)):
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ, name)
        if input_data:
            os.set_blocking(proc.stdin.fileno(), False)
            selector.register(proc.stdin, selectors.EVENT_WRITE, "stdin")
            pending = memoryview(input_data)
        else:
            proc.stdin.close()
            pending = memoryview(b"")
        while selector.get_map():
            if cancel and cancel.is_set():
                raise ExecutionFailure("Execution cancelled or lease lost")
            if time.monotonic() >= deadline:
                raise ExecutionFailure("Execution exceeded its wall-clock limit")
            for key, _ in selector.select(timeout=.1):
                if key.data == "stdin":
                    try:
                        written = os.write(key.fd, pending[:65536])
                        pending = pending[written:]
                    except BrokenPipeError:
                        pending = memoryview(b"")
                    if not pending:
                        selector.unregister(key.fileobj)
                        key.fileobj.close()
                    continue
                chunk = os.read(key.fd, 65536)
                if not chunk:
                    selector.unregister(key.fileobj)
                    key.fileobj.close()
                    continue
                streams[key.data].extend(chunk)
                if sum(len(s) for s in streams.values()) > max_output:
                    raise ExecutionFailure("Execution output exceeded its size limit")
        remaining = max(.01, deadline - time.monotonic())
        code = proc.wait(timeout=remaining)
        return code, bytes(streams["stdout"]), bytes(streams["stderr"])
    except BaseException:
        terminate(proc)
        raise
    finally:
        selector.close()
        for stream in (proc.stdin, proc.stdout, proc.stderr):
            if stream and not stream.closed:
                stream.close()


def execute(policy: RunnerPolicy, path: str, *, cancel=None, credentials=None, evaluator_input=None):
    credentials = credentials or {}
    argv, container_name = policy.command(path, evaluator=evaluator_input is not None, extra_env=credentials)
    safe_env = {k: os.environ[k] for k in ("PATH", "LANG", "LC_ALL", "SYSTEMROOT", "TMPDIR", "VIRTUAL_ENV") if k in os.environ}
    safe_env.update({"PYTHONDONTWRITEBYTECODE": "1", "PYTHONUNBUFFERED": "1", **credentials})
    if policy.mode == "trusted-process":
        # Supports source checkouts without passing the host's arbitrary PYTHONPATH to tested code.
        safe_env["PYTHONPATH"] = str(Path(__file__).resolve().parent.parent)
    try:
        code, out, err = bounded_execute(argv, env=safe_env, cwd=policy.workspace, timeout=policy.timeout,
            max_output=policy.max_output_bytes, cancel=cancel,
            input_data=json.dumps(evaluator_input).encode() if evaluator_input is not None else b"")
        if code:
            # Child logs can contain secrets; only bounded redacted excerpts are returned.
            from ordeal_agent.privacy import redact
            raise ExecutionFailure(f"Child exited {code}: " + redact(err.decode(errors="replace")[-2000:]))
        try:
            report = json.loads(out)
        except ValueError as exc:
            raise ExecutionFailure("Child did not return a valid JSON report") from exc
        if not isinstance(report, dict):
            raise ExecutionFailure("Child report must be an object")
        return report
    finally:
        if container_name:
            try:
                subprocess.run(["docker", "rm", "-f", container_name], stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL, timeout=15, check=False)
            except (OSError, subprocess.TimeoutExpired):
                pass


class CustomerRunner:
    def __init__(self, client: PlatformClient, policy: RunnerPolicy, *, name="customer-runner", labels=None, custom_evaluators=False):
        self.client, self.policy = client, policy
        self.custom_evaluators = custom_evaluators
        self.labels = list(labels or [])
        if custom_evaluators and "custom-evaluator" not in self.labels:
            self.labels.append("custom-evaluator")
        self.registration = self.client.request("POST", "/api/v1/runners", {"name": name, "labels": self.labels})

    def run_once(self):
        claim = self.client.request("POST", "/api/v1/jobs/claim", {"runner_id": self.registration["id"],
            "kinds": ["suite", "evaluate"] if self.custom_evaluators else ["suite"]})
        job = claim["job"]
        if not job:
            return None
        lease = {"runner_id": self.registration["id"], "lease_token": job["lease_token"]}
        cancelled, done = threading.Event(), threading.Event()
        interval = max(.25, min(20, (job["lease_until"] - time.time()) / 3))

        def heartbeat():
            while not done.wait(interval):
                try:
                    self.client.request("POST", f"/api/v1/jobs/{job['id']}/heartbeat", lease)
                except Exception:
                    cancelled.set()  # Fail closed on loss of the control-plane lease.
                    return
        heart = threading.Thread(target=heartbeat, daemon=True)
        heart.start()
        try:
            credentials = self.client.request("POST", f"/api/v1/jobs/{job['id']}/credentials", lease)["environment"] if job["payload"].get("secret_refs") else {}
            if job["kind"] == "suite":
                report = execute(self.policy, job["payload"]["suite_path"], cancel=cancelled, credentials=credentials)
                result = {"report": report}
                cost = report.get("usage", {}).get("cost_usd") or 0
            else:
                spec = self.client.request("GET", f"/api/v1/resources/{job['payload']['evaluator_id']}?version={job['payload']['version']}")
                if spec["data"].get("type") != "python":
                    raise ExecutionFailure("Customer custom-evaluator runner only accepts Python evaluator jobs")
                trace = self.client.request("GET", f"/api/v1/traces/{job['payload']['trace_id']}")
                path, _, function = spec["data"]["entrypoint"].partition(":")
                evaluated = execute(self.policy, path, cancel=cancelled, credentials=credentials,
                    evaluator_input={"function": function, "trace": trace["data"], "options": spec["data"].get("options", {})})
                result = {"evaluation": evaluated, "trace_hash": trace["content_hash"]}
                cost = 0
            if cancelled.is_set():
                raise ExecutionFailure("Lease lost before submission")
            response = self.client.request("POST", f"/api/v1/jobs/{job['id']}/complete", {**lease, "result": result, "actual_cost_usd": cost})
            return {"job_id": job["id"], "state": response["state"]}
        except Exception as exc:
            try:
                self.client.request("POST", f"/api/v1/jobs/{job['id']}/fail", {**lease,
                    "message": type(exc).__name__ + ": " + str(exc)[:1800], "retryable": not isinstance(exc, ExecutionFailure)})
            except Exception:
                pass  # An expired or cancelled lease must not overwrite the newer owner.
            raise
        finally:
            done.set()
            heart.join(timeout=2)


def internal_work_once(database, settings, ident, runner_id=None):
    with database.tx(ident.tenant_id) as conn:
        if runner_id is None:
            registration = services.register_runner(conn, ident, schemas.RunnerRegister(name="internal-evaluator", labels=["internal-evaluator"]))
            runner_id = registration["id"]
        job = services.claim(conn, ident, schemas.Claim(runner_id=runner_id, kinds=["evaluate"]), settings)
    if not job:
        return None
    lease = schemas.Lease(runner_id=runner_id, lease_token=job["lease_token"])
    try:
        outcome = evaluate_trace(database, settings, ident, job["payload"]["trace_id"], job["payload"]["evaluator_id"], job["payload"]["version"])
        with database.tx(ident.tenant_id) as conn:
            completed = services.complete(conn, ident, job["id"], schemas.Complete(**lease.model_dump(),
                result={"evaluation": outcome["result"], "persisted_evaluation_id": outcome["id"]}), settings)
        return {"job_id": job["id"], "state": completed["state"], "evaluation_id": outcome["id"]}
    except Exception as exc:
        with database.tx(ident.tenant_id) as conn:
            try:
                services.validate_lease(conn, ident, job["id"], lease)
            except Exception:
                return {"job_id": job["id"], "state": "lease_lost"}
            conn.execute(db.jobs.update().where(db.jobs.c.id == job["id"]).values(state="failed", error=type(exc).__name__, lease_token=None, lease_until=None))
            db.audit_event(conn, ident.tenant_id, ident.id, "job.failed", job["id"], {"error_type": type(exc).__name__})
        return {"job_id": job["id"], "state": "failed"}
