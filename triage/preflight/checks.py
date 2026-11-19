"""
Author: L. Saetta
Date Last Modified: 2026-11-19
License: MIT
Description: The preflight checks, in the order they run. Each check verifies
    one thing and, when it fails, says how to fix it:

     1. LocalEnvironmentCheck   Python and the required packages (environment.py)
     2. ConfigurationCheck      config/settings.toml and the secrets in .env
     3. OciCredentialsCheck     the OCI SDK profile can be loaded
     4. ProjectCheck            project ACTIVE, sandboxes enabled, DENY_ALL network
     5. PermissionsCheck        sandboxes can be listed; leftovers reported
     6. ModelCheck              the model answers and calls a tool
     7. SandboxStartCheck       a real sandbox reaches RUNNING
     8. RuntimeToolsCheck       the tools the agent needs are in the runtime
     9. FileTransferCheck       a file round-trips intact
    10. NetworkIsolationCheck   outbound traffic is blocked
    11. CleanupCheck            the test sandbox is deleted (always runs)
"""

import hashlib
import os
import time

from oci.exceptions import ServiceError

from triage.llm import ModelClientFactory
from triage.preflight.base import Check, CheckResult, PreflightContext
from triage.sandbox import SandboxSession

#: Lifetime of the test sandbox: short, so a leftover ends on its own.
TEST_SANDBOX_MINUTES = 10

#: Tools the agent needs in the sandbox, and tools that are nice to have.
REQUIRED_TOOLS = ("python3", "grep", "awk", "sed", "timeout")
OPTIONAL_TOOLS = ("unzip", "curl")

#: Size of the file used for the round-trip test.
PROBE_FILE_BYTES = 1024 * 1024
PROBE_FILE_PATH = "/workspace/preflight/probe.bin"

#: A script run in the sandbox: an outbound HTTPS request that must fail
#: in a DENY_ALL project. It prints OPEN or BLOCKED and the reason.
NETWORK_PROBE_PATH = "/workspace/preflight/network_probe.py"
NETWORK_PROBE_SCRIPT = """\
import urllib.request

try:
    urllib.request.urlopen("https://example.com", timeout=5)
    print("OPEN")
except Exception as error:
    print("BLOCKED", type(error).__name__, str(error)[:120])
"""

#: A function tool the model must call in the model check.
TIME_TOOL = {
    "type": "function",
    "name": "get_utc_time",
    "description": "Return the current date and time in UTC.",
    "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
}


class ConfigurationCheck(Check):
    """The settings are complete and valid."""

    name = "Configuration"

    def run(self, context: PreflightContext) -> CheckResult:
        problems = context.settings.validate()
        if problems:
            return CheckResult.failed(
                "; ".join(problems),
                "Edit config/settings.toml; put the API key in .env "
                "(start from .env.example).",
            )
        return CheckResult.passed(context.settings.describe())


class OciCredentialsCheck(Check):
    """The OCI SDK profile loads and its key (or session token) is readable.

    This check is local. Whether the service accepts the signature is seen
    by the next check, the first call to OCI.
    """

    name = "OCI credentials"

    def run(self, context: PreflightContext) -> CheckResult:
        settings = context.settings
        try:
            _ = context.clients.config
            _ = context.clients.signer
        except Exception as error:  # pylint: disable=broad-exception-caught
            return CheckResult.failed(
                f"{type(error).__name__}: {error}",
                f"Check {settings.oci_config_file}, profile [{settings.oci_profile}] "
                f"and oci.auth = {settings.oci_auth.value!r}. For session tokens run: "
                f"oci session authenticate --profile-name {settings.oci_profile}",
            )
        return CheckResult.passed(
            f"profile {settings.oci_profile} ({settings.oci_auth.value})"
        )


class ProjectCheck(Check):
    """The project is ACTIVE, has sandboxes enabled and denies outbound traffic."""

    name = "Project"

    def run(self, context: PreflightContext) -> CheckResult:
        project_id = context.settings.oci_project_id
        try:
            project = context.clients.generative_ai.get_generative_ai_project(
                project_id
            ).data
        except ServiceError as error:
            return CheckResult.failed(
                f"HTTP {error.status}: {error.code}", _hint(error)
            )
        context.project = project

        state = str(project.lifecycle_state).upper()
        if state != "ACTIVE":
            return CheckResult.failed(
                f"project is {state}", "Wait until the project is ACTIVE."
            )
        config = project.sandbox_config
        if config is None or not config.is_enabled:
            return CheckResult.failed(
                "sandboxes are not enabled in this project",
                "Create a project with config/sandbox-project-deny-all.json (Lab 0).",
            )

        network = _network_policy(config)
        detail = f"{project.display_name}: ACTIVE, sandboxes enabled, network {network}"
        if network != "MANAGED/DENY_ALL":
            return CheckResult.failed(
                detail,
                "The demo needs outbound traffic denied. Network settings cannot be "
                "changed: create a new project with "
                "config/sandbox-project-deny-all.json (Lab 0).",
                blocking=False,
            )
        return CheckResult.passed(detail)


class PermissionsCheck(Check):
    """The user can list the sandboxes of the project."""

    name = "Permissions"

    def run(self, context: PreflightContext) -> CheckResult:
        try:
            running = context.manager.list_running()
        except ServiceError as error:
            return CheckResult.failed(
                f"HTTP {error.status}: {error.code}", _hint(error)
            )
        if running:
            names = ", ".join(sandbox.display_name for sandbox in running[:3])
            return CheckResult.warning(
                f"{len(running)} sandbox(es) already RUNNING: {names}",
                "Leftovers from earlier runs use quota. Delete them if not needed.",
            )
        return CheckResult.passed("list_sandboxes OK, no sandbox running")


class ModelCheck(Check):
    """The model answers through the Responses API and calls a function tool."""

    name = "Model"

    def run(self, context: PreflightContext) -> CheckResult:
        settings = context.settings
        client = ModelClientFactory(settings).create()
        started = time.monotonic()
        try:
            response = client.responses.create(
                model=settings.model_id,
                input="What time is it now in UTC? Use the get_utc_time tool.",
                tools=[TIME_TOOL],
                store=False,
            )
        except Exception as error:  # pylint: disable=broad-exception-caught
            return CheckResult.failed(
                f"{type(error).__name__}: {str(error)[:160]}",
                "Check model.model_id, the API key in .env and, for OCI, that the model "
                "is available in oci.region.",
            )
        elapsed = time.monotonic() - started
        called = [item for item in response.output if item.type == "function_call"]
        if not called:
            return CheckResult.failed(
                f"the model answered without calling the tool ({elapsed:.1f} s)",
                "The agent needs function calling: choose another model.",
            )
        return CheckResult.passed(
            f"{settings.model_id} called {called[0].name} ({elapsed:.1f} s)"
        )


class SandboxStartCheck(Check):
    """A real sandbox can be created and reaches RUNNING."""

    name = "Sandbox"

    def run(self, context: PreflightContext) -> CheckResult:
        started = time.monotonic()
        try:
            context.session = context.manager.create(
                display_name=f"preflight-{time.strftime('%Y%m%d-%H%M%S')}",
                lifetime_minutes=TEST_SANDBOX_MINUTES,
            )
        except ServiceError as error:
            return CheckResult.failed(
                f"HTTP {error.status}: {error.code}", _hint(error)
            )
        elapsed = time.monotonic() - started
        details = context.session.details()
        return CheckResult.passed(
            f"RUNNING in {elapsed:.1f} s ({details.runtime}, {details.shape})"
        )


class RuntimeToolsCheck(Check):
    """The runtime has the tools the agent needs, and /workspace is writable."""

    name = "Runtime tools"

    def run(self, context: PreflightContext) -> CheckResult:
        tools = REQUIRED_TOOLS + OPTIONAL_TOOLS
        probe = (
            f"for t in {' '.join(tools)}; do "
            'if command -v "$t" >/dev/null 2>&1; then echo "$t yes"; '
            'else echo "$t no"; fi; done; '
            "touch /workspace/.probe && echo 'workspace yes' || echo 'workspace no'"
        )
        result = _session(context).run(probe, timeout_seconds=30)
        found = dict(
            line.split(" ", 1) for line in result.output.splitlines() if " " in line
        )

        if found.get("workspace") != "yes":
            return CheckResult.failed("/workspace is not writable", blocking=True)
        missing = [tool for tool in REQUIRED_TOOLS if found.get(tool) != "yes"]
        if missing:
            return CheckResult.failed(
                f"missing: {', '.join(missing)}",
                "Use a runtime that includes them (sandbox.runtime).",
                blocking=False,
            )
        absent = [tool for tool in OPTIONAL_TOOLS if found.get(tool) != "yes"]
        detail = " ".join(REQUIRED_TOOLS)
        if absent:
            return CheckResult.warning(f"{detail} (not found: {', '.join(absent)})")
        return CheckResult.passed(f"{detail} {' '.join(OPTIONAL_TOOLS)}")


class FileTransferCheck(Check):
    """A binary file written to the sandbox reads back identical."""

    name = "File transfer"

    def run(self, context: PreflightContext) -> CheckResult:
        session = _session(context)
        data = os.urandom(PROBE_FILE_BYTES)
        started = time.monotonic()
        session.write_file(PROBE_FILE_PATH, data)
        echoed = session.read_file(PROBE_FILE_PATH)
        elapsed = time.monotonic() - started
        if hashlib.sha256(echoed).digest() != hashlib.sha256(data).digest():
            return CheckResult.failed(
                f"read back {len(echoed)} bytes, content differs", blocking=False
            )
        return CheckResult.passed(
            f"1 MiB written and read back intact ({elapsed:.1f} s)"
        )


class NetworkIsolationCheck(Check):
    """An outbound HTTPS request from the sandbox fails."""

    name = "Network isolation"

    def run(self, context: PreflightContext) -> CheckResult:
        session = _session(context)
        session.write_file(NETWORK_PROBE_PATH, NETWORK_PROBE_SCRIPT)
        result = session.run(f"python3 {NETWORK_PROBE_PATH}", timeout_seconds=30)
        output = result.output.strip()
        if output.startswith("OPEN"):
            return CheckResult.failed(
                "outbound traffic is ALLOWED (https://example.com answered)",
                "Use a project whose network policy is DENY_ALL (Lab 0).",
                blocking=False,
            )
        if output.startswith("BLOCKED"):
            reason = output.removeprefix("BLOCKED").strip()
            return CheckResult.passed(
                f"outbound blocked in {result.duration_seconds:.1f} s ({reason})"
            )
        return CheckResult.warning(f"unexpected probe output: {output[:120]!r}")


class CleanupCheck(Check):
    """The test sandbox is stopped and deleted, unless --keep was given."""

    name = "Cleanup"
    always_run = True

    def run(self, context: PreflightContext) -> CheckResult:
        session = context.session
        if session is None:
            return CheckResult.skipped("no sandbox was created")
        if context.keep_sandbox:
            return CheckResult.warning(
                f"sandbox kept: {session.sandbox_id}",
                f"It expires on its own after {TEST_SANDBOX_MINUTES} minutes.",
            )
        session.close()
        return CheckResult.passed("test sandbox stopped and deleted")


# ---------------------------------------------------------------- helpers


def _session(context: PreflightContext) -> SandboxSession:
    """The test sandbox; the checks that use it run after SandboxStartCheck."""
    if context.session is None:
        raise RuntimeError("no test sandbox: SandboxStartCheck did not run")
    return context.session


def _network_policy(sandbox_config) -> str:
    """The outbound network setting of a project, for example MANAGED/DENY_ALL."""
    networking = sandbox_config.sandbox_outbound_networking_config
    if networking is None:
        return "not configured"
    mode = str(networking.network_mode).upper()
    policy = getattr(networking, "network_policy", None)
    return f"{mode}/{str(policy.mode).upper()}" if policy is not None else mode


def _hint(error: ServiceError) -> str:
    """How to fix a service error, from its HTTP status."""
    if error.status == 401:
        return (
            "Authentication failed: check the key and fingerprint in the OCI "
            "profile, or refresh the session token (oci session refresh)."
        )
    if error.status == 404:
        return (
            "Not found or not authorized: check oci.project_id, that oci.region is "
            "the project's region, and the IAM policies (manage "
            "generative-ai-sandbox, read generative-ai-project)."
        )
    if error.status == 429:
        return "Throttled or out of quota: check the sandbox service limits."
    return str(error.message)
