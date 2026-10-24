"""Log line normalisation. Raw GitHub logs prefix every line with an ISO timestamp.

The extractor and the rules engine must agree on what a line looks like, so this
lives in one place instead of being re-derived by each of them.
"""

import re

TIMESTAMP_PREFIX = re.compile(r"^\d{4}-\d{2}-\d{2}T[\d:.]+Z\s*")


def strip_timestamp(line: str) -> str:
    return TIMESTAMP_PREFIX.sub("", line)


def normalise(text: str) -> str:
    """Remove leading timestamps so `^`-anchored patterns match real log lines."""
    return "\n".join(strip_timestamp(line) for line in text.splitlines())
