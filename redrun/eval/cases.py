"""The evaluation set: what each case is, and which half of the split it is in.

The split is chosen by a seeded draw and then frozen in `eval/cases.yaml`. A test
recomputes it from the seed and fails if the file disagrees, so the holdout cannot
be quietly re-cut after someone has seen the results.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from redrun.config import ROOT
from redrun.core.taxonomy import Cause

CASES_PATH = ROOT / "eval" / "cases.yaml"
HOLDOUT_SIZE = 5
SPLITS = ("dev", "holdout")


class CaseError(ValueError):
    """The case file is malformed, or a case contradicts the rules for the set."""


@dataclass(frozen=True)
class Case:
    id: str
    url: str
    expected_cause: Cause
    root_cause_keywords: tuple[str, ...]
    split: str
    constructed: bool
    notes: str = ""

    @property
    def sample(self) -> str:
        """The recorded sample directory this case replays from."""
        return f"eval_{self.id}"

    @property
    def is_holdout(self) -> bool:
        return self.split == "holdout"


@dataclass(frozen=True)
class CaseSet:
    seed: int
    cases: tuple[Case, ...]
    notes: str = ""

    def __iter__(self):
        return iter(self.cases)

    def __len__(self) -> int:
        return len(self.cases)

    def selected(self, split: str) -> list[Case]:
        if split == "all":
            return list(self.cases)
        if split not in SPLITS:
            raise CaseError(f"unknown split {split!r}; expected one of {SPLITS} or 'all'")
        return [case for case in self.cases if case.split == split]

    def by_id(self, case_id: str) -> Case:
        for case in self.cases:
            if case.id == case_id:
                return case
        raise KeyError(case_id)

    @property
    def causes(self) -> set[Cause]:
        return {case.expected_cause for case in self.cases}


def choose_holdout(cases: list[Case] | tuple[Case, ...], seed: int) -> tuple[str, ...]:
    """Pick five holdout cases, one per cause, with no two from the same cause.

    The set has six non-unknown causes and five holdout slots, so one cause keeps
    all of its cases in dev. Which five, and which case of each, comes from the
    seed and from nothing else. `unknown` is never a holdout case: there is no
    second way to be unknown, so holding it out would prove nothing.
    """
    rng = random.Random(seed)
    by_cause: dict[str, list[str]] = {}
    for case in cases:
        if case.expected_cause is Cause.UNKNOWN:
            continue
        by_cause.setdefault(case.expected_cause.value, []).append(case.id)
    causes = sorted(by_cause)
    if len(causes) < HOLDOUT_SIZE:
        raise CaseError(
            f"the set has {len(causes)} non-unknown causes; cannot draw {HOLDOUT_SIZE}"
        )
    rng.shuffle(causes)
    return tuple(sorted(rng.choice(by_cause[cause]) for cause in causes[:HOLDOUT_SIZE]))


def _parse(raw: dict[str, Any]) -> Case:
    try:
        cause = Cause(str(raw["expected_cause"]))
    except (KeyError, ValueError) as exc:
        raise CaseError(f"case {raw.get('id')!r} has no usable expected_cause: {exc}") from exc
    keywords = raw.get("root_cause_keywords") or []
    if not isinstance(keywords, list) or not 2 <= len(keywords) <= 4:
        raise CaseError(
            f"case {raw.get('id')!r} needs 2-4 root_cause_keywords, got {keywords!r}"
        )
    split = str(raw.get("split") or "")
    if split not in SPLITS:
        raise CaseError(f"case {raw.get('id')!r} has split {split!r}; expected {SPLITS}")
    return Case(
        id=str(raw["id"]),
        url=str(raw.get("url") or ""),
        expected_cause=cause,
        root_cause_keywords=tuple(str(word).lower() for word in keywords),
        split=split,
        constructed=bool(raw.get("constructed")),
        notes=str(raw.get("notes") or "").strip(),
    )


def load(path: Path | None = None) -> CaseSet:
    """Read the case file and check it still agrees with its own seed."""
    target = Path(path) if path else CASES_PATH
    if not target.exists():
        raise CaseError(f"no case file at {target}")
    payload = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict):
        raise CaseError(f"{target} must hold a mapping")
    seed = payload.get("seed")
    if not isinstance(seed, int):
        raise CaseError(f"{target} must carry an integer seed")
    raw_cases = payload.get("cases") or []
    if not raw_cases:
        raise CaseError(f"{target} lists no cases")
    cases = tuple(_parse(raw) for raw in raw_cases)
    ids = [case.id for case in cases]
    if len(set(ids)) != len(ids):
        raise CaseError(f"duplicate case ids in {target}")
    expected = choose_holdout(cases, seed)
    actual = tuple(sorted(case.id for case in cases if case.is_holdout))
    if expected != actual:
        raise CaseError(
            "the holdout in the file is not the holdout the seed picks: "
            f"file={list(actual)} seed={list(expected)}"
        )
    return CaseSet(seed=seed, cases=cases, notes=str(payload.get("notes") or "").strip())


def sample_dir(settings, case: Case) -> Path:
    return Path(settings.samples_dir) / case.sample
