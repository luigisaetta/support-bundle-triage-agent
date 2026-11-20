"""
Author: L. Saetta
Date Last Modified: 2026-11-20
License: MIT
Description: Unit tests of the bundle checks and of the upload to the fake
    sandbox: valid bundles are extracted under /workspace/bundle, dangerous
    ones are rejected before anything is uploaded.
"""

# pylint: disable=missing-function-docstring

import stat
import zipfile

import pytest

from tests.unit.fakes import FakeSandboxClient
from triage import ingest
from triage.ingest import BundleError, BundleInspector, BundleUploader
from triage.sandbox import SandboxSession


@pytest.fixture(name="client")
def fixture_client(tmp_path):
    root = tmp_path / "sandbox_root"
    root.mkdir()
    return FakeSandboxClient(root)


@pytest.fixture(name="session")
def fixture_session(client):
    return SandboxSession(client, "sbx-fake")


def _zip(path, entries):
    """Write a zip with {name: text} entries."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as archive:
        for name, text in entries.items():
            archive.writestr(name, text)
    return path


def test_upload_extracts_the_bundle_in_the_sandbox(tmp_path, client, session):
    bundle = _zip(tmp_path / "in/b.zip", {"logs/app.log": "a\nb\n", "README": "x"})
    summary = BundleUploader(session).upload(bundle)
    assert summary.files == 2 and summary.extracted_bytes == 5
    assert (client.workspace / "bundle/logs/app.log").read_text() == "a\nb\n"
    assert not (client.workspace / "upload/bundle.zip").exists()


def test_upload_replaces_the_previous_bundle(tmp_path, client, session):
    BundleUploader(session).upload(_zip(tmp_path / "in/1.zip", {"old.log": "x"}))
    BundleUploader(session).upload(_zip(tmp_path / "in/2.zip", {"new.log": "y"}))
    assert not (client.workspace / "bundle/old.log").exists()
    assert (client.workspace / "bundle/new.log").exists()


@pytest.mark.parametrize("name", ["../evil.txt", "a/../../evil.txt", "/etc/evil"])
def test_unsafe_paths_are_rejected(tmp_path, name):
    with pytest.raises(BundleError, match="unsafe path"):
        BundleInspector().inspect(_zip(tmp_path / "b.zip", {name: "x"}))


def test_symbolic_links_are_rejected(tmp_path):
    path = tmp_path / "b.zip"
    with zipfile.ZipFile(path, "w") as archive:
        info = zipfile.ZipInfo("link")
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(info, "/etc/passwd")
    with pytest.raises(BundleError, match="symbolic link"):
        BundleInspector().inspect(path)


def test_too_many_entries_are_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(ingest, "MAX_MEMBERS", 2)
    bundle = _zip(tmp_path / "b.zip", {"a": "1", "b": "2", "c": "3"})
    with pytest.raises(BundleError, match="more than 2 entries"):
        BundleInspector().inspect(bundle)


def test_zip_bombs_are_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(ingest, "MAX_EXTRACTED_BYTES", 10)
    with pytest.raises(BundleError, match="extracts to more than"):
        BundleInspector().inspect(_zip(tmp_path / "b.zip", {"a": "x" * 100}))


def test_missing_files_and_non_zip_files_are_rejected(tmp_path):
    with pytest.raises(BundleError, match="does not exist"):
        BundleInspector().inspect(tmp_path / "missing.zip")
    text = tmp_path / "notes.zip"
    text.write_text("not a zip")
    with pytest.raises(BundleError, match="not a zip"):
        BundleInspector().inspect(text)


def test_a_rejected_bundle_is_not_uploaded(tmp_path, client, session):
    with pytest.raises(BundleError):
        BundleUploader(session).upload(_zip(tmp_path / "b.zip", {"../x": "1"}))
    assert "run" not in client.calls
