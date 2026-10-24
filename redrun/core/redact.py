"""Secret redaction. Runs before an excerpt is stored, traced or sent to a model."""

import math
import re

REDACTED = "[REDACTED]"

_PRIVATE_KEY = re.compile(
    r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?-----END [A-Z0-9 ]*PRIVATE KEY-----",
    re.DOTALL,
)
_BEARER = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9\-._~+/]+=*")
_TOKEN_PATTERNS = (
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{16,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{16,}\b"),
    re.compile(r"\bnvapi-[A-Za-z0-9_\-]{12,}\b"),
    re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
)
# KEY=..., TOKEN=..., DB_PASSWORD: ... and friends.
_KV = re.compile(
    r"(?i)\b([A-Z0-9_]*(?:KEY|TOKEN|SECRET|PASSWORD|PASSWD|PWD|CREDENTIAL)[A-Z0-9_]*)"
    r"\s*[=:]\s*([^\s'\"]+)"
)
_ENTROPY_CANDIDATE = re.compile(r"[A-Za-z0-9+/=_\-]{33,}")


def _shannon_entropy(value: str) -> float:
    if not value:
        return 0.0
    counts = {ch: value.count(ch) for ch in set(value)}
    total = len(value)
    return -sum((n / total) * math.log2(n / total) for n in counts.values())


def _looks_like_secret(token: str) -> bool:
    """Long and random: mixed case, a digit, and enough entropy to not be a name."""
    has_upper = any(c.isupper() for c in token)
    has_lower = any(c.islower() for c in token)
    has_digit = any(c.isdigit() for c in token)
    if not (has_upper and has_lower and has_digit):
        return False
    if _shannon_entropy(token) < 3.5:
        return False
    return True


def redact(text: str) -> str:
    """Mask every known secret shape in `text`. Safe to call twice."""
    if not text:
        return text

    out = _PRIVATE_KEY.sub(REDACTED, text)
    out = _BEARER.sub(f"Bearer {REDACTED}", out)
    for pattern in _TOKEN_PATTERNS:
        out = pattern.sub(REDACTED, out)
    out = _KV.sub(lambda m: f"{m.group(1)}={REDACTED}", out)
    out = _ENTROPY_CANDIDATE.sub(
        lambda m: REDACTED if _looks_like_secret(m.group(0)) else m.group(0), out
    )
    return out


def redact_data(value: object) -> object:
    """Redact every string inside a JSON-shaped value, keys left alone."""
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, dict):
        return {k: redact_data(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact_data(v) for v in value]
    return value
