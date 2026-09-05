Deploy target info
```mediaclipmakarr:
    allowed_branches:
    - claude/*
    - main
    allowed_env_files:
    - deploy.env```
```

Confirm deploy worked by navigating to `http://192.168.0.111:3623`
Always create branches under `claude\*`

Only run the full test suite before a commit, push, deploy. It doesn't need run every change for every turn.

If frontend validation needs more than ~3 tool calls without a clear pass/fail signal, or the behavior is timing/race-dependent, stop and hand the user specific test cases instead of continuing to iterate.

When reviewing code, and planning features, evaluate threats with the understanding that the estimated max daily concurrent user count for the application is between 0 and 1. 