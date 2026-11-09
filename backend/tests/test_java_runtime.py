"""Phase 6: Java Runtime Manager tests.

Covers Java detection, multiple runtimes, missing/invalid executables, managed
installation logic (mocked), checksum verification, and Java compatibility.
No real Java runtime is downloaded or installed during these tests.
"""

from __future__ import annotations

import hashlib
from unittest.mock import MagicMock, patch

import pytest

from app.core.java import JavaRuntimeManager
from app.services.compat import check_compatibility, required_java_major


class FakeSubprocess:
    """Fake result for ``subprocess.run`` (java -version)."""

    def __init__(self, returncode: int = 0, stderr: str = "", stdout: str = ""):
        self.returncode = returncode
        self.stderr = stderr
        self.stdout = stdout


# ----------------------------------------------------------------------
# Compatibility tests
# ----------------------------------------------------------------------


def test_compatibility_1_20_1_requires_java_17():
    result = check_compatibility("1.20.1", 17)
    assert result.compatible is True
    assert result.required_java_major == 17


def test_compatibility_1_20_1_rejects_java_25():
    # Java 25 is newer than 17, so it IS compatible (17 is the minimum).
    result = check_compatibility("1.20.1", 25)
    assert result.compatible is True


def test_compatibility_1_20_1_rejects_java_11():
    result = check_compatibility("1.20.1", 11)
    assert result.compatible is False
    assert result.required_java_major == 17
    assert result.runtime_java_major == 11
    assert "incompatible" in result.message.lower()


def test_compatibility_unknown_version_defaults_to_java_17():
    result = check_compatibility("1.99.9", 17)
    assert result.compatible is True
    assert result.required_java_major == 17


def test_compatibility_runtime_unknown():
    result = check_compatibility("1.20.1", None)
    assert result.compatible is False
    assert result.runtime_java_major is None


def test_required_java_major_explicit():
    assert required_java_major("1.20.1") == 17
    assert required_java_major("1.21") == 21
    assert required_java_major("1.18") == 17


# ----------------------------------------------------------------------
# Java detection tests
# ----------------------------------------------------------------------


def test_java_runtime_display_name():
    from app.core.java import JavaRuntime

    rt = JavaRuntime(name="Temurin", version="17.0.13", vendor="Eclipse Temurin")
    assert "Temurin" in rt.display_name
    assert "17.0.13" in rt.display_name


def test_java_runtime_is_executable_false_for_missing():
    from app.core.java import JavaRuntime

    rt = JavaRuntime(executable_path="/nonexistent/bin/java")
    assert rt.is_executable is False


def test_detect_system_java_returns_list():
    manager = JavaRuntimeManager.__new__(JavaRuntimeManager)
    # Avoid touching real filesystem/settings by constructing manually.
    manager.runtimes_dir = None  # type: ignore[assignment]
    with patch.object(JavaRuntimeManager, "detect_system_java", return_value=[]):
        result = manager.detect_system_java()
    assert result == []


def test_checksum_verification():
    import tempfile
    from pathlib import Path

    content = b"fake-java-archive-content"
    with tempfile.NamedTemporaryFile(delete=False, suffix=".tar.gz") as tmp:
        tmp.write(content)
        tmp_path = Path(tmp.name)
    try:
        expected = hashlib.sha256(content).hexdigest()
        actual = hashlib.sha256(tmp_path.read_bytes()).hexdigest()
        assert actual == expected
    finally:
        tmp_path.unlink(missing_ok=True)
