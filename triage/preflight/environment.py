"""
Author: L. Saetta
Date Last Modified: 2026-11-19
License: MIT
Description: The first preflight check: the running Python and the required
    packages. It uses only the standard library, so it can run, and say what
    is wrong, before any required package is imported. A frequent cause of
    failure is a different Python environment than the one prepared for the
    lab, so the report always shows which Python is running.
"""

import importlib.util
import sys
from importlib import metadata

from triage.preflight.base import Check, CheckResult, PreflightContext

#: Minimum Python version.
MIN_PYTHON = (3, 11)

#: Minimum versions of the required packages.
MIN_PACKAGES = {"oci": (2, 189, 0), "openai": (2, 0, 0), "python-dotenv": (1, 0, 0)}

#: Module that only recent OCI SDKs ship: the sandbox client.
SANDBOX_MODULE = "oci.generative_ai_sandbox"


class LocalEnvironmentCheck(Check):
    """Python is recent enough and the required packages are installed."""

    name = "Local environment"

    def run(self, context: PreflightContext | None = None) -> CheckResult:
        python = f"Python {sys.version.split()[0]} ({sys.executable})"
        wrong_env = (
            "Check that the lab's Python environment is active (which python), "
            "then run: pip install -r requirements.txt"
        )
        if sys.version_info < MIN_PYTHON:
            return CheckResult.failed(
                f"{python}: {'.'.join(map(str, MIN_PYTHON))} or later is required",
                wrong_env,
            )

        versions = []
        for package, minimum in MIN_PACKAGES.items():
            try:
                installed = metadata.version(package)
            except metadata.PackageNotFoundError:
                return CheckResult.failed(
                    f"{package} is not installed in {python}", wrong_env
                )
            if _version_tuple(installed) < minimum:
                return CheckResult.failed(
                    f"{package} {installed} is too old in {python}; "
                    f"{'.'.join(map(str, minimum))} or later is required",
                    wrong_env,
                )
            versions.append(f"{package} {installed}")

        if importlib.util.find_spec(SANDBOX_MODULE) is None:
            return CheckResult.failed(
                f"{SANDBOX_MODULE} is missing in {python}",
                "Reinstall the OCI SDK: pip install -U -r requirements.txt",
            )
        return CheckResult.passed(f"{python}, " + ", ".join(versions))


def _version_tuple(version: str) -> tuple[int, ...]:
    """A version string as a tuple of integers, for example (2, 189, 0)."""
    parts = []
    for part in version.split("."):
        digits = "".join(ch for ch in part if ch.isdigit())
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts)
