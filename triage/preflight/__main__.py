"""
Author: L. Saetta
Date Last Modified: 2026-11-19
License: MIT
Description: Command line entry point of the preflight check. Verifies, in
    about a minute, that everything the lab needs is in place: local
    packages, settings, OCI credentials, the Generative AI project, the
    model, and a real sandbox (created, probed and deleted).

Usage (from the repository root):

    python -m triage.preflight            # all checks
    python -m triage.preflight --quick    # no sandbox is created
    python -m triage.preflight --keep     # leave the test sandbox running

Exit code: 0 when no check failed, 1 otherwise.
"""

import argparse
import sys

from triage.config import Settings
from triage.preflight.base import Check, PreflightContext, Status
from triage.preflight.checks import (
    CleanupCheck,
    ConfigurationCheck,
    FileTransferCheck,
    LocalEnvironmentCheck,
    ModelCheck,
    NetworkIsolationCheck,
    OciCredentialsCheck,
    PermissionsCheck,
    ProjectCheck,
    RuntimeToolsCheck,
    SandboxStartCheck,
)
from triage.preflight.runner import ConsoleReporter, PreflightRunner


def build_checks(quick: bool) -> list[Check]:
    """The checks to run, in order.

    Args:
        quick: Leave out the checks that create a sandbox.

    Returns:
        The checks.
    """
    checks: list[Check] = [
        LocalEnvironmentCheck(),
        ConfigurationCheck(),
        OciCredentialsCheck(),
        ProjectCheck(),
        PermissionsCheck(),
        ModelCheck(),
    ]
    if not quick:
        checks += [
            SandboxStartCheck(),
            RuntimeToolsCheck(),
            FileTransferCheck(),
            NetworkIsolationCheck(),
            CleanupCheck(),
        ]
    return checks


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse the command line.

    Args:
        argv: The arguments; ``sys.argv[1:]`` when omitted.
    """
    parser = argparse.ArgumentParser(
        prog="python -m triage.preflight",
        description="Check that the environment is ready for the lab.",
    )
    parser.add_argument(
        "--quick", action="store_true", help="skip the checks that create a sandbox"
    )
    parser.add_argument(
        "--keep", action="store_true", help="leave the test sandbox running at the end"
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run the preflight check.

    Args:
        argv: The arguments; ``sys.argv[1:]`` when omitted.

    Returns:
        The process exit code: 0 when no check failed, 1 otherwise.
    """
    args = parse_args(argv)
    context = PreflightContext(settings=Settings.load(), keep_sandbox=args.keep)
    runner = PreflightRunner(build_checks(args.quick), ConsoleReporter())
    print()
    results = runner.run(context)
    return 1 if any(result.status is Status.FAIL for result in results) else 0


if __name__ == "__main__":
    sys.exit(main())
