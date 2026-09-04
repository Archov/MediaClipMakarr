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

If you're having any difficulty validating frontend behavior, ask the user to run specific tests instead of iterating endlessly