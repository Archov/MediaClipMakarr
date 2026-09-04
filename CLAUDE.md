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

If frontend validation needs more than ~3 tool calls without a clear pass/fail signal, or the behavior is timing/race-dependent, stop and hand the user specific test cases instead of continuing to iterate.