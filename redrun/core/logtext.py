"""Log line normalisation. Raw GitHub logs prefix every line with an ISO timestamp.

The extractor and the rules engine must agree on what a line looks like, so this
lives in one place instead of being re-derived by each of them.
"""

import re

TIMESTAMP_PREFIX = re.compile(r"^\d{4}-\d{2}-\d{2}T[\d:.]+Z\s*")
# Colour codes: cargo, pytest and npm all colour their errors in CI, and an escape in
# front of `error[E0432]` hides the line from every `^`-anchored pattern.
ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def strip_timestamp(line: str) -> str:
    return TIMESTAMP_PREFIX.sub("", line)


def strip_ansi(line: str) -> str:
    return ANSI_ESCAPE.sub("", line)


def clean_line(line: str) -> str:
    """A log line as a person reads it: no timestamp, no colour codes."""
    return strip_ansi(strip_timestamp(line))


def normalise(text: str) -> str:
    """Remove leading timestamps so `^`-anchored patterns match real log lines."""
    return "\n".join(clean_line(line) for line in text.splitlines())
