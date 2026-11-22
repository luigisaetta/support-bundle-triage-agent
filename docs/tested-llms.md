# Tested LLMs

Working record of manual tests on the workshop sample support bundle.
Results below were reported by the workshop author and can be updated as
additional models and runs are evaluated.

| Model | Provider / region | Result | Root cause identification | Evidence items reported | Evidence verified | Speed | Notes |
|-------|-------------------|--------|---------------------------|-------------------------|-------------------|-------|-------|
| `openai.gpt-6.1-sol` | Not recorded | OK | Correct | 13 | Not recorded | Fast | Correctly identifies the root cause and reports 13 evidence items. |
| `openai.gpt-6.1-terra` | OCI Generative AI / Chicago (`us-chicago-1`) | OK | Correct | 8 | 8/8 | Fast | Reports high confidence. Identifies CHG-4821, the reduction from 1,500 to 300 processes, the restart and next-morning exhaustion, and the aggregate pool maximum of 450 connections. Explicitly states that process consumers and a possible connection leak are not established by the bundle. |
| `openai.gpt-oss-120b` | OCI Generative AI / Chicago (`us-chicago-1`) | Partial | Identifies the process limit, but misses the causal change and its timing | 6 | 6/6 | Not recorded | Reports high confidence. Connects `processes=300` to ORA-00020 and HikariCP connection pool failures, but does not read all files or identify the change ID and when it occurred. |
| `xai.grok-4.7` | OCI Generative AI / Chicago (`us-chicago-1`) | OK | Correct | 14 | 14/14 | Slower | Latest run reports high confidence. Identifies CHG-4821, the reduction from 1,500 to 300 processes and from 2,272 to 472 sessions, the overnight restart on 2026-11-18, morning process saturation, and the aggregate pool maximum of 450 connections. Connects ORA-00020 to 30-second Hikari timeouts. Generates and executes analysis code, contributing to the longer run time. |
| `gemini-2.5-flash` | OCI Generative AI / Chicago (`us-chicago-1`) | Failed | Incorrect | 3 | 3/3 | Not recorded | Reports medium confidence. Incorrectly attributes the incident to G1 garbage collection pauses on app-node1 and app-node2, a red herring in the sample bundle. Misses CHG-4821, the reduced Oracle process limit and the resulting ORA-00020 connection failures. |

Evidence counts describe each report, not coverage against a fixed set of 13
expected items. More evidence items alone do not imply a better diagnosis.
Verification totals are recorded when supplied in the run output.

These are qualitative observations from manual runs, not a controlled benchmark.
Record provider, region and speed when available, and add or update rows as
further tests are completed.
