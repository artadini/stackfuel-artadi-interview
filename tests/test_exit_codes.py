"""Exit codes of the command line: 0 = SUCCESS, 1 = FAILED, 2 = PARTIAL_SUCCESS."""

import pytest

from pipeline_functions.helpers.extraction_types import DatasetResult
from pipeline_functions.workflows import runner


def summary_with(*statuses: str) -> dict:
    results = [
        DatasetResult(f"dataset_{i}", status, "full")
        for i, status in enumerate(statuses)
    ]
    return {"raw_results": results}


def test_a_complete_run_exits_normally(monkeypatch):
    monkeypatch.setattr(
        runner, "run_pipeline", lambda args: summary_with("LOADED", "NO_CHANGES")
    )
    runner.main([])  # no SystemExit


def test_a_layer_that_only_curates_exits_normally(monkeypatch):
    monkeypatch.setattr(runner, "run_pipeline", lambda args: summary_with())
    runner.main(["--layer", "curated"])


def test_a_partial_run_exits_with_code_2(monkeypatch):
    monkeypatch.setattr(
        runner, "run_pipeline", lambda args: summary_with("LOADED", "FAILED")
    )
    with pytest.raises(SystemExit) as exit_info:
        runner.main([])
    assert exit_info.value.code == runner.EXIT_PARTIAL_SUCCESS == 2


def test_a_failed_run_exits_with_code_1(monkeypatch):
    def broken(args):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(runner, "run_pipeline", broken)
    with pytest.raises(SystemExit) as exit_info:
        runner.main([])
    assert exit_info.value.code == runner.EXIT_FAILED == 1
