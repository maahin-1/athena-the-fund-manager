## Problem

<!-- What is wrong or missing, and for whom? Link the issue if there is one. -->

## Final behaviour

<!-- What does the code do now? Describe observable behaviour, not the diff. -->

## Tests and evidence

- [ ] A test fails without this change and passes with it (name it):
- [ ] Focused checks run (commands and result):
- [ ] Offline only: no live LLM, broker or data-provider call, no production store read
- [ ] No test was skipped, excluded, retried until green or loosened to get this passing (if an exclusion changed, say which and why)

## Not covered

<!-- Be specific about what this change does not verify. -->

## Compatibility and data

- [ ] No change to a stored format, schema or snapshot contract, or the change is described here with its migration and rollback
- [ ] No runtime data, credentials, build output or large dataset added
- [ ] Documentation updated where behaviour changed

## Rollback

<!-- How is this undone safely? -->
