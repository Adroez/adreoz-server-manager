"""Java / Minecraft version compatibility.

A maintainable mapping of Minecraft versions to their minimum required Java
major version. Designed to be independently testable without network access.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

logger = logging.getLogger("asm.compat")

# Minimum Java major version required by each Minecraft release.
# Keyed by Minecraft version string (e.g. "1.20.1"). The special key "__all__"
# is not used; unknown versions fall back to the `default_java_major`.
MINECRAFT_JAVA_REQUIREMENTS: dict[str, int] = {
    # Java 16
    "1.17": 16,
    "1.17.1": 16,
    # Java 17
    "1.18": 17,
    "1.18.1": 17,
    "1.18.2": 17,
    "1.19": 17,
    "1.19.1": 17,
    "1.19.2": 17,
    "1.19.3": 17,
    "1.19.4": 17,
    "1.20": 17,
    "1.20.1": 17,
    "1.20.2": 17,
    "1.20.3": 17,
    "1.20.4": 17,
    "1.20.5": 17,
    "1.20.6": 17,
    # Java 21
    "1.21": 21,
    "1.21.1": 21,
    "1.21.2": 21,
    "1.21.3": 21,
    "1.21.4": 21,
}

# Default Java requirement for versions not explicitly mapped. Newer Minecraft
# versions generally move to newer Java, so a conservative default of 17 keeps
# the panel from silently selecting an incompatible old runtime.
DEFAULT_JAVA_MAJOR = 17


@dataclass
class CompatibilityResult:
    compatible: bool
    minecraft_version: str
    required_java_major: int
    runtime_java_major: int | None
    message: str


def _parse_minecraft_major_minor(version: str) -> tuple[int, int] | None:
    """Parse ``X.Y`` or ``X.Y.Z`` into a comparable tuple."""
    match = re.match(r"^(\d+)\.(\d+)", version)
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def required_java_major(minecraft_version: str) -> int:
    """Return the required Java major version for a Minecraft version."""

    if minecraft_version in MINECRAFT_JAVA_REQUIREMENTS:
        return MINECRAFT_JAVA_REQUIREMENTS[minecraft_version]

    # For versions not explicitly mapped, use the default. Callers can log a
    # warning when this path is hit so the mapping can be updated.
    logger.warning(
        "No explicit Java requirement for Minecraft %s; defaulting to Java %d.",
        minecraft_version,
        DEFAULT_JAVA_MAJOR,
    )
    return DEFAULT_JAVA_MAJOR


def check_compatibility(
    minecraft_version: str, runtime_java_major: int | None
) -> CompatibilityResult:
    """Check whether a Java runtime is compatible with a Minecraft version.

    Args:
        minecraft_version: The Minecraft version string (e.g. "1.20.1").
        runtime_java_major: The major version of the Java runtime, or None if
            unknown (system default).

    Returns:
        A :class:`CompatibilityResult` describing compatibility.
    """

    required = required_java_major(minecraft_version)

    if runtime_java_major is None:
        return CompatibilityResult(
            compatible=False,
            minecraft_version=minecraft_version,
            required_java_major=required,
            runtime_java_major=None,
            message=(
                f"Java runtime version could not be determined. "
                f"Minecraft {minecraft_version} requires Java {required}. "
                "Select a known Java runtime before starting."
            ),
        )

    if runtime_java_major < required:
        return CompatibilityResult(
            compatible=False,
            minecraft_version=minecraft_version,
            required_java_major=required,
            runtime_java_major=runtime_java_major,
            message=(
                f"Java {runtime_java_major} is incompatible with Minecraft "
                f"{minecraft_version}, which requires Java {required} or newer."
            ),
        )

    return CompatibilityResult(
        compatible=True,
        minecraft_version=minecraft_version,
        required_java_major=required,
        runtime_java_major=runtime_java_major,
        message=(
            f"Java {runtime_java_major} is compatible with Minecraft "
            f"{minecraft_version} (requires Java {required})."
        ),
    )
