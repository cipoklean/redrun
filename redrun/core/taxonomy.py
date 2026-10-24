"""The cause taxonomy. Frozen: agents and the frontend both index on these values."""

from enum import Enum


class Cause(str, Enum):
    """Why a run failed. `unknown` is a real answer, not a placeholder."""

    BAD_COMMIT = "bad_commit"
    DEPENDENCY_BUMP = "dependency_bump"
    FLAKY_TEST = "flaky_test"
    INFRA_OUTAGE = "infra_outage"
    EXPIRED_OR_MISSING_SECRET = "expired_or_missing_secret"
    CONFIG_CHANGE = "config_change"
    UNKNOWN = "unknown"
