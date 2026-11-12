"""Java runtime detection and management.

Discovers Java runtimes available on the system, validates executables, and
manages an application-managed runtime directory under ``data/runtimes``.

The system Java (e.g. ``/usr/bin/java``) is detected but never modified,
replaced, or removed. Managed runtimes (such as Eclipse Temurin) are
downloaded, verified, and extracted into the application data directory only.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from app.core.config import Settings

logger = logging.getLogger("asm.java")


@dataclass
class JavaRuntime:
    """A discovered or installed Java runtime."""

    id: Optional[int] = None
    name: str = ""
    vendor: str = ""
    version: str = ""
    major_version: int = 0
    executable_path: str = ""
    source_url: Optional[str] = None
    checksum: Optional[str] = None
    checksum_algorithm: Optional[str] = None
    install_status: str = "installed"
    installed_at: Optional[str] = None
    is_managed: bool = False

    @property
    def display_name(self) -> str:
        """Human-friendly runtime label."""
        if self.name:
            return f"{self.name} {self.version}".strip()
        return f"{self.vendor} {self.version}".strip() or self.executable_path

    @property
    def is_executable(self) -> bool:
        path = Path(self.executable_path)
        return path.is_file() and os.access(path, os.X_OK)


class JavaRuntimeError(RuntimeError):
    """Raised when a Java runtime cannot be discovered or used."""


class JavaRuntimeManager:
    """Discovers and validates Java runtimes on the host."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.runtimes_dir = settings.data_dir / "runtimes"
        self.runtimes_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.runtimes_dir.chmod(0o700)

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    def detect_system_java(self) -> list[JavaRuntime]:
        """Detect Java executables on PATH and common JDK locations.

        The system runtime is reported as-is; it is never modified or removed.
        """

        found: list[JavaRuntime] = []
        seen_paths: set[str] = set()

        candidates: list[str] = []

        # PATH-resolved java.
        java_on_path = shutil.which("java")
        if java_on_path:
            candidates.append(java_on_path)

        # Common system JDK locations.
        for jvm_root in [
            Path("/usr/lib/jvm"),
            Path("/usr/local/lib/jvm"),
            Path("/opt/java"),
        ]:
            if not jvm_root.is_dir():
                continue
            for entry in sorted(jvm_root.iterdir()):
                java_bin = entry / "bin" / "java"
                if java_bin.is_file():
                    candidates.append(str(java_bin))
                # Also check bin/java directly under the root.
                direct = jvm_root / entry.name / "bin" / "java"
                if direct.is_file():
                    candidates.append(str(direct))

        # Application-managed runtimes.
        if self.runtimes_dir.is_dir():
            for entry in sorted(self.runtimes_dir.iterdir()):
                java_bin = entry / "bin" / "java"
                if java_bin.is_file():
                    candidates.append(str(java_bin))

        for candidate in candidates:
            resolved = str(Path(candidate).resolve())
            if resolved in seen_paths:
                continue
            seen_paths.add(resolved)

            runtime = self._probe_java(resolved)
            if runtime is not None:
                found.append(runtime)

        return found

    def _probe_java(self, executable_path: str) -> Optional[JavaRuntime]:
        """Run ``java -version`` and parse the result."""

        try:
            result = subprocess.run(
                [executable_path, "-version"],
                capture_output=True,
                text=True,
                timeout=15,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            logger.warning("Failed to probe Java at %s: %s", executable_path, exc)
            return None

        # java -version prints to stderr.
        output = (result.stderr or result.stdout or "").strip()
        if not output:
            return None

        version = self._parse_java_version(output)
        vendor = self._parse_java_vendor(output)
        name = f"{vendor} Java" if vendor else "Java"

        runtime = JavaRuntime(
            name=name,
            vendor=vendor,
            version=version,
            major_version=self._major_version(version),
            executable_path=executable_path,
            install_status="installed",
            is_managed=False,
        )
        return runtime

    @staticmethod
    def _parse_java_version(output: str) -> str:
        """Parse a version string from ``java -version`` output.

        Handles both legacy (``1.8.0_...``) and modern
        (``25.0.5...`` / ``17.0.13...``) version lines.
        """

        import re

        for line in output.splitlines():
            # openjdk version "25.0.5.0.3" 2026-10-24
            match = re.search(r'version\s+"([^"]+)"', line)
            if match:
                return match.group(1)
        return "unknown"

    @staticmethod
    def _parse_java_vendor(output: str) -> str:
        """Identify the JDK vendor from ``java -version`` output.

        Every vendor prints an ``openjdk version ...`` first line, so the
        specific markers must be checked across the whole output before
        falling back to generic OpenJDK.
        """

        lowered = output.lower()

        # Order matters: more specific vendors first.
        markers: tuple[tuple[str, str], ...] = (
            ("temurin", "Eclipse Temurin"),
            ("eclipse adoptium", "Eclipse Temurin"),
            ("adoptopenjdk", "AdoptOpenJDK"),
            ("graalvm", "GraalVM"),
            ("zulu", "Azul Zulu"),
            ("azul", "Azul Zulu"),
            ("corretto", "Amazon Corretto"),
            ("microsoft", "Microsoft OpenJDK"),
            ("sapmachine", "SAP SapMachine"),
            ("liberica", "BellSoft Liberica"),
            ("bellsoft", "BellSoft Liberica"),
            ("ibm", "IBM Semeru"),
            ("semeru", "IBM Semeru"),
            ("oracle", "Oracle"),
        )
        for marker, name in markers:
            if marker in lowered:
                return name

        if "openjdk" in lowered:
            return "OpenJDK"
        return "Unknown"

    @staticmethod
    def _major_version(version: str) -> int:
        """Return the Java feature release (8, 11, 17, 21, ...)."""

        import re

        value = version.strip()
        # Legacy scheme: 1.8.0_402 -> 8. Must be checked before the generic
        # numeric match, which would otherwise report 1.
        legacy = re.match(r"1\.(\d+)", value)
        if legacy:
            return int(legacy.group(1))
        match = re.match(r"(\d+)", value)
        if match:
            return int(match.group(1))
        return 0

    # ------------------------------------------------------------------
    # Managed runtime helpers
    # ------------------------------------------------------------------

    def runtime_path(self, name: str) -> Path:
        """Return the directory for a managed runtime, validating the name."""

        # Prevent path traversal: only a single path component is allowed.
        if not name or name != name.strip() or "/" in name or "\\" in name or name in (".", ".."):
            raise JavaRuntimeError(f"Invalid runtime name: {name!r}")
        return self.runtimes_dir / name

    def list_managed(self) -> list[JavaRuntime]:
        """List runtimes installed under the managed directory."""

        found: list[JavaRuntime] = []
        if not self.runtimes_dir.is_dir():
            return found
        for entry in sorted(self.runtimes_dir.iterdir()):
            java_bin = entry / "bin" / "java"
            if not java_bin.is_file():
                continue
            runtime = self._probe_java(str(java_bin.resolve()))
            if runtime is not None:
                runtime.is_managed = True
                runtime.name = entry.name
                found.append(runtime)
        return found
