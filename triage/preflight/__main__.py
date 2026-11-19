"""
Author: L. Saetta
Date Last Modified: 2026-11-19
License: MIT
Description: Command line entry point of the preflight check. Verifies, in
    about a minute, that everything the lab needs is in place: local
    packages, settings, OCI credentials, the Generative AI project, the
    model, and a real sandbox (created, probed and deleted).

    The run has two phases. First the local environment is checked using
    only the standard library. The other checks run only if that first check
    passes, so a missing or outdated package is reported, not a traceback.
    Each check is numbered and announced before it runs.

Usage (from the repository root):

    python -m triage.preflight            # all checks
    python -m triage.preflight --quick    # no sandbox is created
    python -m triage.preflight --keep     # leave the test sandbox running

Exit code: 0 when no check failed, 1 otherwise.
"""

import argparse
import sys

from triage.preflight.base import Check, PreflightContext, Status
from triage.preflight.environment import LocalEnvironmentCheck
from triage.preflight.runner import ConsoleReporter, PreflightRunner


def build_checks(quick: bool) -> list[Check]:
    """The checks that follow the local environment check, in order.

    Their modules import the OCI SDK and openai, so this raises (for
    example ImportError) when a required package is missing or outdated.

    Args:
        quick: Leave out the checks that create a sandbox.

    Returns:
        The checks.
    """
    # pylint: disable-next=import-outside-toplevel
    from triage.preflight.checks import (
        CleanupCheck,
        ConfigurationCheck,
        FileTransferCheck,
        ModelCheck,
        NetworkIsolationCheck,
        OciCredentialsCheck,
        PermissionsCheck,
        ProjectCheck,
        RuntimeToolsCheck,
        SandboxStartCheck,
    )

    checks: list[Check] = [
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
    import_error: Exception | None = None
    try:
        later_checks = build_checks(args.quick)
    except Exception as error:  # pylint: disable=broad-exception-caught
        later_checks, import_error = [], error  # the first check explains it
    reporter = ConsoleReporter(total=1 + len(later_checks))
    print()

    # Phase 1: the local environment, with the standard library only.
    environment = LocalEnvironmentCheck()
    reporter.start(environment)
    result = environment.run()
    reporter.result(environment, result)
    if result.status is Status.FAIL:
        reporter.summary([result])
        return 1
    if import_error is not None:
        raise import_error  # the packages look fine, yet the import failed

    # Phase 2: everything else; the required packages are now known to work.
    from triage.config import Settings  # pylint: disable=import-outside-toplevel

    context = PreflightContext(settings=Settings.load(), keep_sandbox=args.keep)
    results = PreflightRunner(later_checks, reporter).run(context)
    return 1 if any(result.status is Status.FAIL for result in results) else 0


if __name__ == "__main__":
    sys.exit(main())
