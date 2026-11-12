"""Validation for per-server JVM arguments.

Server owners may supply extra JVM flags (heap size, garbage collector,
system properties). Arguments are passed to the JVM as an argv list, so shell
injection is not possible; the remaining risks are:

* JVM features that load code or read arbitrary files (``@argfile``,
  ``-javaagent``, ``-agentlib``, ``-agentpath``, boot classpath overrides).
  These would let a non-administrator owner execute code as the panel user,
  so they are rejected.
* Resource abuse: unbounded argument counts or lengths.

The validated form is a list of tokens; the stored form is the same tokens
joined by single spaces.
"""

from __future__ import annotations

from dataclasses import dataclass

# Hard limits keep a single server row small and launch commands predictable.
MAX_JVM_ARG_TOKENS = 32
MAX_JVM_ARG_LENGTH = 256
MAX_JVM_ARGS_TOTAL_LENGTH = 1024

# Options that can load code, read files, or weaken the JVM sandbox. Matching
# is case-insensitive and prefix based, so ``-javaagent:foo.jar`` is caught.
FORBIDDEN_PREFIXES: tuple[str, ...] = (
    "@",
    "-javaagent",
    "-agentlib",
    "-agentpath",
    "-agentvm",
    "-xrun",
    "-xbootclasspath",
    "-bootclasspath",
    "-cp",
    "-classpath",
    "--class-path",
    "--module-path",
    "-p",
    "--patch-module",
    "-djava.class.path",
    "-djava.library.path",
    "-djava.security.manager",
    "-djava.security.policy",
    "-djava.security.properties",
    "-djavax.net.ssl.truststore",
    "-djavax.net.ssl.keystore",
    "-duser.home",
    "-duser.dir",
    "-djava.io.tmpdir",
    "-dfile.encoding",
    "-dsun.boot.class.path",
    # These execute an arbitrary command or write attacker-chosen paths.
    "-xx:onerror",
    "-xx:onoutofmemoryerror",
    "-xx:heapdumppath",
    "-xx:errorfile",
    "-xx:sharedarchivefile",
    "-xx:startflightrecording",
)

# A JVM argument must look like an option.
MIN_TOKEN_LENGTH = 2


class JvmArgsError(ValueError):
    """Raised when JVM arguments are malformed or unsafe."""


@dataclass(frozen=True)
class ParsedJvmArgs:
    """Validated JVM arguments."""

    tokens: tuple[str, ...]

    @property
    def normalized(self) -> str:
        return " ".join(self.tokens)


def parse_jvm_args(raw: str | None) -> ParsedJvmArgs:
    """Split and validate a raw JVM argument string.

    Returns an empty result for ``None`` or a blank string. Raises
    :class:`JvmArgsError` with a user-facing message on any problem.
    """

    if raw is None:
        return ParsedJvmArgs(())
    if not isinstance(raw, str):
        raise JvmArgsError("JVM arguments must be a string.")

    if any(character in raw for character in ("\x00", "\n", "\r")):
        raise JvmArgsError("JVM arguments must not contain line breaks or null bytes.")

    stripped = raw.strip()
    if not stripped:
        return ParsedJvmArgs(())

    if len(stripped) > MAX_JVM_ARGS_TOTAL_LENGTH:
        raise JvmArgsError(
            f"JVM arguments are too long (max {MAX_JVM_ARGS_TOTAL_LENGTH} characters)."
        )

    if '"' in stripped or "'" in stripped:
        raise JvmArgsError(
            "JVM arguments must not contain quotes; separate arguments with spaces."
        )

    tokens = stripped.split()
    if len(tokens) > MAX_JVM_ARG_TOKENS:
        raise JvmArgsError(
            f"Too many JVM arguments (max {MAX_JVM_ARG_TOKENS})."
        )

    for token in tokens:
        if len(token) < MIN_TOKEN_LENGTH or not token.startswith("-"):
            raise JvmArgsError(
                f"'{token}' is not a JVM option. Every argument must start with '-'."
            )
        if len(token) > MAX_JVM_ARG_LENGTH:
            raise JvmArgsError(
                f"JVM argument '{token[:32]}...' is too long "
                f"(max {MAX_JVM_ARG_LENGTH} characters)."
            )
        lowered = token.lower()
        for prefix in FORBIDDEN_PREFIXES:
            if lowered.startswith(prefix):
                raise JvmArgsError(
                    f"JVM argument '{token}' is not allowed for security reasons."
                )

    return ParsedJvmArgs(tuple(tokens))


def validate_jvm_args(raw: str | None) -> str | None:
    """Validate and normalise JVM arguments for storage.

    Returns ``None`` when no arguments are configured.
    """

    parsed = parse_jvm_args(raw)
    if not parsed.tokens:
        return None
    return parsed.normalized
