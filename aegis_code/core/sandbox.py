"""Docker-based sandbox runner with network isolation and resource limits."""

from __future__ import annotations

import os
import shutil
import tarfile
import tempfile
import time
import io
from pathlib import Path
from dataclasses import dataclass, field

from aegis_code.utils.logger import get_logger

log = get_logger("sandbox")

_BASE_IMAGE = "python:3.11-slim"
_AEGIS_IMAGE_PREFIX = "aegis-sandbox"


@dataclass
class SandboxResult:
    """Container for the results of a sandbox test run."""

    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool = False
    duration_seconds: float = 0.0
    error: str = ""

    @property
    def passed(self) -> bool:
        return self.exit_code == 0 and not self.timed_out


class SandboxRunner:
    """Manages Docker-based isolated test execution.

    Features:
      • Auto-detects ``requirements.txt`` / ``pyproject.toml`` and caches deps.
      • Disables networking during the test run.
      • Applies CPU and memory limits.
    """

    def __init__(
        self,
        *,
        cpu_period: int = 100_000,
        cpu_quota: int = 100_000,  # 1 CPU
        mem_limit: str = "512m",
        timeout: int = 180,
    ) -> None:
        self.cpu_period = cpu_period
        self.cpu_quota = cpu_quota
        self.mem_limit = mem_limit
        self.timeout = timeout
        self._client = None

    # ── Docker client ─────────────────────────────────────────

    @property
    def client(self):
        if self._client is None:
            try:
                import docker

                self._client = docker.from_env()
                self._client.ping()
                log.info("Docker daemon connected")
            except Exception as exc:
                raise RuntimeError(
                    f"Cannot connect to Docker daemon. Is Docker running?\n{exc}"
                ) from exc
        return self._client

    # ── Image building ────────────────────────────────────────

    def _detect_dep_file(self, target_path: Path) -> str | None:
        """Return the dep file name present in *target_path*, if any."""
        for name in ("requirements.txt", "pyproject.toml"):
            if (target_path / name).exists():
                return name
        return None

    def _build_image(self, target_path: Path) -> str:
        """Build a Docker image with cached dependencies."""
        dep_file = self._detect_dep_file(target_path)
        tag = f"{_AEGIS_IMAGE_PREFIX}:{hash(str(target_path)) & 0xFFFFFFFF:08x}"

        # Compose Dockerfile
        lines = [
            f"FROM {_BASE_IMAGE}",
            "WORKDIR /workspace",
            "RUN pip install --no-cache-dir pytest pytest-asyncio pytest-timeout",
        ]

        if dep_file == "requirements.txt":
            lines.append("COPY requirements.txt /workspace/requirements.txt")
            lines.append(
                "RUN pip install --no-cache-dir -r /workspace/requirements.txt "
                "|| echo 'WARNING: some deps failed to install'"
            )
        elif dep_file == "pyproject.toml":
            lines.append("COPY pyproject.toml /workspace/pyproject.toml")
            # Also copy setup files if they exist
            lines.append(
                "RUN pip install --no-cache-dir /workspace "
                "|| pip install --no-cache-dir -e /workspace "
                "|| echo 'WARNING: pyproject.toml install failed'"
            )

        lines.append("COPY . /workspace")
        dockerfile_content = "\n".join(lines) + "\n"

        # Create build context as a tar archive
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)

            # Write Dockerfile
            (tmpdir_path / "Dockerfile").write_text(dockerfile_content)

            # Copy target directory contents into the build context
            for item in target_path.iterdir():
                src = target_path / item.name
                dst = tmpdir_path / item.name
                if item.name.startswith(".") and item.name != ".env":
                    continue
                if item.is_dir():
                    shutil.copytree(src, dst, dirs_exist_ok=True)
                else:
                    shutil.copy2(src, dst)

            log.info("Building sandbox image: %s", tag)
            image, build_logs = self.client.images.build(
                path=str(tmpdir_path),
                tag=tag,
                rm=True,
                forcerm=True,
            )
            for chunk in build_logs:
                if "stream" in chunk:
                    line = chunk["stream"].strip()
                    if line:
                        log.debug("  build: %s", line)

        return tag

    # ── Test execution ────────────────────────────────────────

    def _make_tar(self, files: dict[str, str]) -> bytes:
        """Create an in-memory tar archive from {filename: content} dict."""
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as tar:
            for name, content in files.items():
                data = content.encode("utf-8")
                info = tarfile.TarInfo(name=name)
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
        return buf.getvalue()

    def run_tests(
        self,
        target_path: Path,
        test_files: dict[str, str],
        *,
        existing_tests_dir: str | None = None,
        extra_pytest_args: list[str] | None = None,
    ) -> SandboxResult:
        """Run tests inside an isolated Docker container.

        Parameters
        ----------
        target_path:
            Path to the target project being analysed.
        test_files:
            Mapping of ``{filename: content}`` for adversarial test files
            to inject into the container.
        existing_tests_dir:
            Relative path within the target where existing tests live
            (e.g. ``"tests"``). These are run alongside the adversarial ones
            during verification.
        extra_pytest_args:
            Additional CLI flags passed to ``pytest``.
        """
        try:
            image_tag = self._build_image(target_path)
        except Exception as exc:
            return SandboxResult(
                exit_code=-1,
                stdout="",
                stderr=str(exc),
                error=f"Image build failed: {exc}",
            )

        # Determine pytest targets
        test_targets = []
        adversarial_dir = "/workspace/_aegis_tests"

        if existing_tests_dir:
            test_targets.append(f"/workspace/{existing_tests_dir}")
        test_targets.append(adversarial_dir)

        pytest_cmd_parts = [
            "python",
            "-m",
            "pytest",
            "-v",
            "--tb=long",
            f"--timeout={self.timeout - 30}",
            "-x",  # stop on first failure for efficiency
        ]
        if extra_pytest_args:
            pytest_cmd_parts.extend(extra_pytest_args)
        pytest_cmd_parts.extend(test_targets)
        pytest_cmd = " ".join(pytest_cmd_parts)

        cmd = f"mkdir -p {adversarial_dir} && {pytest_cmd}"

        container = None
        start = time.monotonic()
        try:
            # Create container with network disabled
            container = self.client.containers.create(
                image_tag,
                command=["bash", "-c", cmd],
                network_disabled=True,
                cpu_period=self.cpu_period,
                cpu_quota=self.cpu_quota,
                mem_limit=self.mem_limit,
                working_dir="/workspace",
                detach=True,
            )

            # Inject adversarial tests into the container
            if test_files:
                prefixed = {
                    f"_aegis_tests/{name}": content
                    for name, content in test_files.items()
                }
                tar_data = self._make_tar(prefixed)
                container.put_archive("/workspace", tar_data)

            container.start()
            result = container.wait(timeout=self.timeout)
            exit_code = result.get("StatusCode", -1)

            stdout = container.logs(stdout=True, stderr=False).decode(
                "utf-8", errors="replace"
            )
            stderr = container.logs(stdout=False, stderr=True).decode(
                "utf-8", errors="replace"
            )
            duration = time.monotonic() - start

            return SandboxResult(
                exit_code=exit_code,
                stdout=stdout,
                stderr=stderr,
                timed_out=False,
                duration_seconds=round(duration, 2),
            )

        except Exception as exc:
            duration = time.monotonic() - start
            err_name = type(exc).__name__
            is_timeout = "timeout" in err_name.lower() or "read timed out" in str(exc).lower()
            return SandboxResult(
                exit_code=-1,
                stdout="",
                stderr=str(exc),
                timed_out=is_timeout,
                duration_seconds=round(duration, 2),
                error=str(exc),
            )
        finally:
            if container:
                try:
                    container.remove(force=True)
                except Exception:
                    pass

    # ── Cleanup ───────────────────────────────────────────────

    def cleanup_images(self) -> None:
        """Remove all Aegis sandbox images."""
        try:
            images = self.client.images.list(name=_AEGIS_IMAGE_PREFIX)
            for img in images:
                self.client.images.remove(img.id, force=True)
                log.debug("Removed image %s", img.short_id)
        except Exception as exc:
            log.warning("Image cleanup failed: %s", exc)
