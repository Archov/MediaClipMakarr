Deploy target info
```mediaclipmakarr:
    allowed_branches:
    - claude/*
    - main
    allowed_env_files:
    - deploy.env```
```

Confirm deploy worked by navigating to `https://mcm.capsule-corp.me/` using the Claude in Chrome browser extension (the `mcp__claude-in-chrome__*` tools — the user's real, logged-in Chrome), not the sandboxed Browser pane. Check that the app loads and that the served JS bundle filename (`/assets/index-*.js`) matches the hash from the deploy's own build output.
Always create branches under `claude\*`

Only run the full test suite before a commit, push, deploy. It doesn't need run every change for every turn.

If frontend validation needs more than ~3 tool calls without a clear pass/fail signal, or the behavior is timing/race-dependent, stop and hand the user specific test cases instead of continuing to iterate.

When reviewing code, and planning features, evaluate threats with the understanding that the estimated max daily concurrent user count for the application is between 0 and 1. 

Review / Fix Acceptance Policy

Optimize for shipping a reliable, polished 1.0 — not theoretical correctness.

Fix a finding when at least one of these is true:

- A normal user has a realistic chance of encountering it through the supported UI and normal usage.
- It can cause incorrect visible behavior on the happy path.
- It can cause loss or corruption of user data.
- Malformed/corrupt media or external data can cause persistent database corruption, destructive state changes, or damage beyond simply failing that operation.
- It involves an irreversible/destructive operation where a plausible failure could affect the wrong user data.

Do NOT fix a finding merely because it is technically possible.

Generally ignore:

- Deliberately malformed API requests that the UI will never generate.
- "A user could type absurd garbage into Postman" scenarios.
- Corrupt/impossible media metadata when the worst result is that the operation fails.
- Concurrency races requiring workloads far beyond the application's realistic deployment.
- Scalability concerns unsupported by the expected number of users.
- Defensive validation whose only benefit is producing a prettier error for impossible input.
- Test hygiene, test completeness, or stricter assertions unless they protect a realistic high-impact regression.
- Speculative performance optimizations.
- Architectural generalization for hypothetical future requirements.
- Extra guards, abstractions, quotas, retries, caches, or recovery machinery for events that are extremely unlikely in real usage.

A finding being easy to fix is not, by itself, a reason to fix it. Every added branch, validator, abstraction, test, and recovery path increases maintenance cost and codebase complexity.

For every review finding, first answer:
1. What realistic sequence of ordinary user actions causes this?
2. How likely is that sequence?
3. What actually happens to the user if it occurs?
4. Is the result persistent/destructive, or can they simply retry?
5. Does the proposed fix add more complexity than the problem justifies?

If there is no convincing realistic scenario, resolve it as accepted risk / won't fix.

Do not let perfect be the enemy of good enough.