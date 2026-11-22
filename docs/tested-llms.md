# Tested LLMs

Working record of manual tests on the workshop sample support bundle.
Results below were reported by the workshop author and can be updated as
additional models and runs are evaluated.

| Model | Provider / region | Result | Root cause identification | Evidence identified | Speed | Notes |
|-------|-------------------|--------|---------------------------|---------------------|-------|-------|
| `openai.gpt-6.1-sol` | Not recorded | OK | Correct | 13/13 | Fast | Identifies all 13 expected evidence items. |
| `openai.gpt-6.1-terra` | OCI Generative AI / Chicago (`us-chicago-1`) | OK | Correct | Not assessed against the 13 expected items | Fast | Reports high confidence and 8/8 citations verified. Identifies CHG-4821, the reduction from 1,500 to 300 processes, the restart and next-morning exhaustion, and the aggregate pool maximum of 450 connections. Explicitly states that process consumers and a possible connection leak are not established by the bundle. |
| `openai.gpt-oss-120b` | OCI Generative AI / Chicago (`us-chicago-1`) | Partial | Identifies the process limit, but misses the causal change and its timing | 6/13 | Not recorded | Reports high confidence and 6/6 citations verified. Connects `processes=300` to ORA-00020 and HikariCP connection pool failures, but does not read all files or identify the change ID and when it occurred. |
| xAI Grok 4.7 | OCI Generative AI / Chicago (`us-chicago-1`) | OK | Correct | 5/13 | Not recorded | Correctly identifies the root cause, with fewer supporting evidence items. |

“Evidence identified” counts expected evidence items found in the investigation.
It is separate from citation verification, which checks whether the first cited
line exists in the bundle.

These are qualitative observations from manual runs, not a controlled benchmark.
Record provider, region and speed when available, and add or update rows as
further tests are completed.
