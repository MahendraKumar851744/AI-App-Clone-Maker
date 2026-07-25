# Simple exploration

This workflow initializes the shared runtime, reuses or installs the configured
APK, opens it, exports screen context, asks the configured LLM for a safe
decision, validates the action, and repeats until completion or the iteration
limit.

```cmd
.\.runtime\python\Scripts\python.exe -B -m backend.workflows run simple_exploration
```

The project-root `.env` supplies `QWEN_API_KEY` for the default configuration.
