"""
Author: L. Saetta
Date Last Modified: 2026-11-20
License: MIT
Description: Puts a support bundle (a zip file) into the sandbox, safely.

    A bundle comes from a customer system: it is untrusted. It is never
    extracted on the local machine. Locally, only the zip directory is read,
    to reject bundles that are too large or that contain dangerous entries
    (absolute paths, "..", symbolic links, encrypted files). Then the zip is
    uploaded to the sandbox and extracted there, under /workspace/bundle.

Usage:

    summary = BundleUploader(session).upload(Path("bundles/sample.zip"))
"""

import stat
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from triage.sandbox import SandboxSession

#: Where the bundle is extracted in the sandbox.
BUNDLE_DIR = "/workspace/bundle"

#: Where the zip file is uploaded in the sandbox.
UPLOAD_PATH = "/workspace/upload/bundle.zip"

#: Largest zip file accepted, in bytes.
MAX_BUNDLE_BYTES = 200 * 1024 * 1024

#: Largest total size of the extracted files, in bytes (zip bomb guard).
MAX_EXTRACTED_BYTES = 1024 * 1024 * 1024

#: Largest number of entries in the zip.
MAX_MEMBERS = 10_000

#: Seconds allowed to extract the bundle in the sandbox.
EXTRACT_TIMEOUT_SECONDS = 300


class BundleError(ValueError):
    """The bundle is not a valid, safe zip file."""


@dataclass(frozen=True)
class BundleSummary:
    """What a bundle contains.

    Attributes:
        name: File name of the bundle.
        files: Number of files.
        compressed_bytes: Size of the zip file.
        extracted_bytes: Total size of the files once extracted.
    """

    name: str
    files: int
    compressed_bytes: int
    extracted_bytes: int

    def describe(self) -> str:
        """One line for the console, for example "sample.zip: 12 files, 3.1 MB"."""
        megabytes = self.extracted_bytes / (1024 * 1024)
        return f"{self.name}: {self.files} files, {megabytes:.1f} MB extracted"


class BundleInspector:
    """Checks a bundle locally, reading only its zip directory."""

    def inspect(self, path: Path) -> BundleSummary:
        """Check that a bundle is a safe zip file and describe it.

        Args:
            path: The local zip file.

        Returns:
            The number and size of the files.

        Raises:
            BundleError: The file is missing, too large, not a zip, or has a
                dangerous entry.
        """
        if not path.is_file():
            raise BundleError(f"{path} does not exist")
        compressed = path.stat().st_size
        if compressed > MAX_BUNDLE_BYTES:
            raise BundleError(f"{path.name} is larger than {MAX_BUNDLE_BYTES} bytes")
        if not zipfile.is_zipfile(path):
            raise BundleError(f"{path.name} is not a zip file")

        with zipfile.ZipFile(path) as archive:
            members = archive.infolist()
        if len(members) > MAX_MEMBERS:
            raise BundleError(f"{path.name} has more than {MAX_MEMBERS} entries")
        files = [member for member in members if not member.is_dir()]
        for member in files:
            self._check_member(member)
        extracted = sum(member.file_size for member in files)
        if extracted > MAX_EXTRACTED_BYTES:
            raise BundleError(
                f"{path.name} extracts to more than {MAX_EXTRACTED_BYTES} bytes"
            )
        return BundleSummary(path.name, len(files), compressed, extracted)

    @staticmethod
    def _check_member(member: zipfile.ZipInfo) -> None:
        """Reject entries that could escape the bundle directory or hide content."""
        name = member.filename
        parts = PurePosixPath(name.replace("\\", "/")).parts
        if name.startswith(("/", "\\")) or ":" in name.split("/")[0] or ".." in parts:
            raise BundleError(f"unsafe path in the bundle: {name}")
        if stat.S_ISLNK(member.external_attr >> 16):
            raise BundleError(f"symbolic link in the bundle: {name}")
        if member.flag_bits & 0x1:
            raise BundleError(f"encrypted file in the bundle: {name}")


class BundleUploader:
    """Uploads a bundle to the sandbox and extracts it under BUNDLE_DIR.

    Args:
        session: The running sandbox.
        inspector: Checks the bundle before the upload.
    """

    def __init__(
        self, session: SandboxSession, inspector: BundleInspector | None = None
    ) -> None:
        self._session = session
        self._inspector = inspector or BundleInspector()

    def upload(self, path: Path) -> BundleSummary:
        """Check, upload and extract a bundle; a previous bundle is replaced.

        Args:
            path: The local zip file.

        Returns:
            The number and size of the files.

        Raises:
            BundleError: The bundle failed a check, or could not be extracted.
            SandboxError: The sandbox could not be reached.
        """
        summary = self._inspector.inspect(path)
        self._session.run(
            f"rm -rf {BUNDLE_DIR} {UPLOAD_PATH} && mkdir -p {BUNDLE_DIR}",
            timeout_seconds=60,
        )
        self._session.write_file(UPLOAD_PATH, path.read_bytes())
        result = self._session.run(
            f"python3 -m zipfile -e {UPLOAD_PATH} {BUNDLE_DIR} && rm -f {UPLOAD_PATH}",
            timeout_seconds=EXTRACT_TIMEOUT_SECONDS,
        )
        if not result.ok:
            raise BundleError(
                f"extraction failed in the sandbox (exit code {result.exit_code}): "
                f"{result.output[-300:]}"
            )
        return summary
