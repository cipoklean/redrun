"""Secret redaction must mask every documented pattern and leave normal log text alone."""

import pytest

from redrun.core.redact import REDACTED, redact

# Each case: (raw secret, label)
SECRETS = [
    ("ghp_16C7e42F292c6912E7710c838347Ae178B4a", "github personal access token"),
    ("gho_16C7e42F292c6912E7710c838347Ae178B4a", "github oauth token"),
    ("ghs_16C7e42F292c6912E7710c838347Ae178B4a", "github app token"),
    ("github_pat_11ABCDEFG0123456789_abcdefghijklmnop", "github fine grained pat"),
    ("nvapi-AbCdEf1234567890GhIjKlMnOpQrStUvWxYz012345", "nvidia api key"),
    ("AKIAIOSFODNN7EXAMPLE", "aws access key id"),
    ("Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.payload.sig", "bearer token"),
    ("KEY=supersecretvalue", "KEY=value pair"),
    ("TOKEN=abc123def456", "TOKEN=value pair"),
    ("dGhpc2lzYXZlcnlsb25nc2VjcmV0c3RyaW5nZm9ydGVzdGluZzEyMw==", "high entropy blob"),
]


@pytest.mark.parametrize("secret,label", SECRETS)
def test_secret_is_masked(secret: str, label: str) -> None:
    out = redact(f"line before {secret} line after")
    assert REDACTED in out, f"{label} produced no redaction marker: {out!r}"
    assert secret not in out, f"{label} survived redaction: {out!r}"
    assert "line before" in out and "line after" in out


def test_private_key_block_is_masked_as_a_whole() -> None:
    block = (
        "-----BEGIN RSA PRIVATE KEY-----\n"
        "MIIEowIBAAKCAQEA1234567890abcdefghij\n"
        "abcdefghijklmnopqrstuvwxyz0123456789\n"
        "-----END RSA PRIVATE KEY-----"
    )
    out = redact(f"before\n{block}\nafter")
    assert REDACTED in out
    assert "MIIEowIBAAKCAQEA" not in out
    assert "BEGIN RSA PRIVATE KEY" not in out
    assert out.startswith("before")
    assert out.rstrip().endswith("after")


def test_openssh_private_key_is_masked() -> None:
    block = (
        "-----BEGIN OPENSSH PRIVATE KEY-----\n"
        "b3BlbnNzaC1rZXktdjEAAAAABG5vbmUAAAAEbm9uZQ\n"
        "-----END OPENSSH PRIVATE KEY-----"
    )
    assert "b3BlbnNzaC1rZXkt" not in redact(block)


@pytest.mark.parametrize(
    "text",
    [
        "commit 0cc638d0f3a1b2c3d4e5f60718293a4b5c6d7e8f fixed the parser",
        "FAILED tests/test_api.py::test_health - assert 500 == 200",
        "see https://github.com/acme/widgets/actions/runs/12345 for the run",
        "changed .github/workflows/ci.yml",
        "model nvidia/nemotron-3.5-lightning-30b-a3b answered in 1200ms",
        "File \"/app/redrun/core/models.py\", line 42, in parse",
    ],
)
def test_normal_text_is_preserved(text: str) -> None:
    assert redact(text) == text


def test_redaction_is_idempotent() -> None:
    once = redact("token=ghp_16C7e42F292c6912E7710c838347Ae178B4a")
    assert redact(once) == once


def test_long_hex_digest_is_not_mistaken_for_a_secret() -> None:
    sha = "9f187b2899734535800168cacc8a3cd81f4a4a7d"
    assert redact(sha) == sha
