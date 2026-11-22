# Tested LLMs

Working record of manual tests on the workshop sample support bundle.
Results below were reported by the workshop author and can be updated as
additional models and runs are evaluated.

| Model | Provider / region | Result | Root cause identification | Evidence identified | Speed | Notes |
|-------|-------------------|--------|---------------------------|---------------------|-------|-------|
| `openai.gpt-6.1-sol` | Not recorded | OK | Correct | 13/13 | Fast | Identifies all 13 expected evidence items. |
| xAI Grok 4.7 | OCI Generative AI / Chicago (`us-chicago-1`) | OK | Correct | 5/13 | Not recorded | Correctly identifies the root cause, with fewer supporting evidence items. |

“Evidence identified” counts expected evidence items found in the investigation.
It is separate from citation verification, which checks whether the first cited
line exists in the bundle.

These are qualitative observations from manual runs, not a controlled benchmark.
Record provider, region and speed when available, and add or update rows as
further tests are completed.
