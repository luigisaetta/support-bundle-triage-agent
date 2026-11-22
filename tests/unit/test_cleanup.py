"""
Author: L. Saetta
Date Last Modified: 2026-11-22
License: MIT
Description: Unit tests of the leftover-sandbox cleanup: listing (pagination,
    DELETED sandboxes hidden), removal by state without waiting,
    refused requests, and the command line confirmation.
"""

# pylint: disable=missing-function-docstring

from datetime import datetime
import io
from types import SimpleNamespace
from unittest.mock import patch

from oci.exceptions import ServiceError
import pytest

from triage import cleanup
from triage.cleanup import SandboxCleaner
from triage.sandbox import SandboxManager


class ProjectFake:
    """A project with several sandboxes, listed in pages of two.

    Args:
        sandboxes: Display name and state of each sandbox.
    """

    def __init__(self, sandboxes: dict[str, str]) -> None:
        self.states = {f"sbx-{name}": state for name, state in sandboxes.items()}
        self.names = {f"sbx-{name}": name for name in sandboxes}
        self.calls: list[str] = []
        self.refuse: set[str] = set()

    def list_sandboxes(self, project_id, page=None):
        ids = list(self.states)
        start = int(page or 0)
        items = [
            SimpleNamespace(
                id=sandbox_id,
                display_name=self.names[sandbox_id],
                lifecycle_state=self.states[sandbox_id],
                time_created=datetime(2026, 11, 20, 10, index),
            )
            for index, sandbox_id in enumerate(ids[start : start + 2], start)
        ]
        next_page = str(start + 2) if start + 2 < len(ids) else None
        return SimpleNamespace(data=SimpleNamespace(items=items), next_page=next_page)

    def stop_sandbox(self, sandbox_id, details):
        self._check(sandbox_id)
        self.calls.append(f"stop {self.names[sandbox_id]}")
        self.states[sandbox_id] = "STOPPING"

    def delete_sandbox(self, sandbox_id):
        self._check(sandbox_id)
        self.calls.append(f"delete {self.names[sandbox_id]}")
        self.states[sandbox_id] = "DELETED"

    def _check(self, sandbox_id):
        if self.names[sandbox_id] in self.refuse:
            raise ServiceError(409, "Conflict", {}, "refused")


@pytest.fixture(name="project")
def fixture_project():
    return ProjectFake(
        {
            "triage-sample": "RUNNING",
            "preflight-20261120": "STOPPED",
            "triage-old": "FAILED",
            "triage-busy": "STOPPING",
            "triage-gone": "DELETED",
            "support-bundle-triage": "STOPPED",
        }
    )


def make_cleaner(project):
    stream = io.StringIO()
    return SandboxCleaner(SandboxManager(project, "ocid1.project"), stream), stream


def test_find_lists_all_pages_and_hides_deleted(project):
    cleaner, _ = make_cleaner(project)
    names = [item.name for item in cleaner.find()]
    assert names == [
        "triage-sample",
        "preflight-20261120",
        "triage-old",
        "triage-busy",
        "support-bundle-triage",
    ]


def test_clean_deletes_stopped_and_stops_running_without_waiting(project):
    cleaner, stream = make_cleaner(project)
    summary = cleaner.clean(cleaner.find())
    assert project.calls == [
        "stop triage-sample",
        "delete preflight-20261120",
        "delete triage-old",
        "delete support-bundle-triage",
    ]
    assert (summary.deleted, summary.stopping, summary.skipped) == (3, 1, 1)
    assert "run this command again" in stream.getvalue()
    assert "triage-busy: STOPPING, retry later" in stream.getvalue()


def test_a_refused_request_does_not_stop_the_cleanup(project):
    project.refuse = {"preflight-20261120"}
    cleaner, stream = make_cleaner(project)
    summary = cleaner.clean(cleaner.find())
    assert "delete triage-old" in project.calls
    assert summary.skipped == 2
    assert "HTTP 409 Conflict" in stream.getvalue()


def run_main(project, arguments, answer="n"):
    stream = io.StringIO()
    manager = SandboxManager(project, "ocid1.project")
    with (
        patch("triage.cleanup.Settings.load"),
        patch("triage.cleanup.OciClients"),
        patch("triage.cleanup.SandboxManager.from_settings", return_value=manager),
    ):
        status = cleanup.main(arguments, stream, ask=lambda prompt: answer)
    return status, stream.getvalue()


def test_main_only_lists_without_delete(project):
    status, text = run_main(project, [])
    assert status == 0 and project.calls == []
    assert "5 sandbox(es)" in text and "--delete" in text


def test_main_asks_before_removing(project):
    status, text = run_main(project, ["--delete"], answer="n")
    assert status == 1 and project.calls == [] and "Nothing removed" in text
    status, _ = run_main(project, ["--delete"], answer="y")
    assert status == 0 and len(project.calls) == 4


def test_main_yes_skips_the_question(project):
    def never_asked(prompt):
        raise AssertionError(prompt)

    stream = io.StringIO()
    manager = SandboxManager(project, "ocid1.project")
    with (
        patch("triage.cleanup.Settings.load"),
        patch("triage.cleanup.OciClients"),
        patch("triage.cleanup.SandboxManager.from_settings", return_value=manager),
    ):
        assert cleanup.main(["--delete", "--yes"], stream, ask=never_asked) == 0
    assert len(project.calls) == 4


def test_main_reports_a_service_error_on_one_line(project):
    def refuse(**kwargs):
        raise ServiceError(403, "NotAuthorized", {}, "not\nallowed")

    project.list_sandboxes = refuse
    status, text = run_main(project, [])
    assert status == 1 and text == "ERROR: not allowed\n"
