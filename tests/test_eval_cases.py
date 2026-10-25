"""The evaluation set: its shape, and the split that is not allowed to move.

The holdout is drawn from the seed in `eval/cases.yaml`. These tests recompute that
draw, so a case cannot be re-labelled after its results have been seen, and the
frozen list below fails the build if the draw ever changes.
"""

from pathlib import Path

import pytest

from redrun.core.taxonomy import Cause
from redrun.eval import cases as cases_module

FROZEN_HOLDOUT = (
    "bad_commit_rename",
    "config_env",
    "dep_bump_conflict",
    "flaky_timing",
    "infra_outage_django_connection_reset",
)

EXPECTED_SIZE = 15
EXPECTED_CONSTRUCTED = 12


@pytest.fixture(scope="module")
def case_set() -> cases_module.CaseSet:
    return cases_module.load()


def test_the_set_has_fifteen_cases(case_set: cases_module.CaseSet) -> None:
    assert len(case_set) == EXPECTED_SIZE
    assert len({case.id for case in case_set}) == EXPECTED_SIZE


def test_every_cause_in_the_taxonomy_is_covered(case_set: cases_module.CaseSet) -> None:
    assert case_set.causes == set(Cause)


def test_the_holdout_is_five_cases_with_no_cause_twice(case_set: cases_module.CaseSet) -> None:
    holdout = case_set.selected("holdout")
    assert len(holdout) == 5
    assert len({case.expected_cause for case in holdout}) == 5, "one case per cause"


def test_unknown_is_never_a_holdout_case(case_set: cases_module.CaseSet) -> None:
    assert Cause.UNKNOWN not in {case.expected_cause for case in case_set.selected("holdout")}
    assert Cause.UNKNOWN in {case.expected_cause for case in case_set.selected("dev")}


def test_the_holdout_list_has_not_changed(case_set: cases_module.CaseSet) -> None:
    assert tuple(sorted(case.id for case in case_set.selected("holdout"))) == FROZEN_HOLDOUT


def test_the_holdout_is_the_one_the_seed_draws(case_set: cases_module.CaseSet) -> None:
    assert cases_module.choose_holdout(case_set.cases, case_set.seed) == FROZEN_HOLDOUT
    assert case_set.seed == 20261025


def test_a_split_that_disagrees_with_the_seed_is_refused(tmp_path: Path) -> None:
    """Flip one case's split in the file and loading must refuse it."""
    import yaml  # noqa: PLC0415 - only this test rewrites the file

    document = yaml.safe_load(cases_module.CASES_PATH.read_text(encoding="utf-8"))
    dev_case = next(item for item in document["cases"] if item["split"] == "dev")
    dev_case["split"] = "holdout"
    target = tmp_path / "cases.yaml"
    target.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    with pytest.raises(cases_module.CaseError):
        cases_module.load(target)


def test_a_seed_that_draws_a_different_holdout_is_refused(tmp_path: Path) -> None:
    """Change the seed and the frozen split no longer matches the draw."""
    import yaml  # noqa: PLC0415

    document = yaml.safe_load(cases_module.CASES_PATH.read_text(encoding="utf-8"))
    document["seed"] = 1
    target = tmp_path / "cases.yaml"
    target.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    with pytest.raises(cases_module.CaseError):
        cases_module.load(target)


def test_constructed_cases_carry_a_real_run_url(case_set: cases_module.CaseSet) -> None:
    constructed = [case for case in case_set if case.constructed]
    assert len(constructed) == EXPECTED_CONSTRUCTED
    for case in constructed:
        assert case.url.startswith("https://github.com/cipoklean/redrun-lab/actions/runs/"), case.id
    for case in case_set:
        assert case.url.startswith("https://github.com/"), case.id
        assert case.notes.strip(), f"{case.id} needs a note saying why it is a case"


def test_keywords_are_two_to_four_words(case_set: cases_module.CaseSet) -> None:
    for case in case_set:
        assert 2 <= len(case.root_cause_keywords) <= 4, case.id
        assert all(word == word.lower() for word in case.root_cause_keywords), case.id


def test_sample_directories_are_named_after_the_case(case_set: cases_module.CaseSet) -> None:
    for case in case_set:
        assert case.sample == f"eval_{case.id}"


def test_an_unknown_split_is_rejected(case_set: cases_module.CaseSet) -> None:
    with pytest.raises(cases_module.CaseError):
        case_set.selected("testing")
