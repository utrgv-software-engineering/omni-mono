# Otto Code single-agent

This example runs Otto Code as a **single agent** that performs its work in a series of phases.

```bash
python main.py
```

The agent sequentially executes the same scan → explore → code → verify workflow that was previously implemented using multiple sub‑agents, but all within a single assistant.

The available tools include `create_pull_request`, which pushes the current
branch and opens a draft pull request using the ``gh`` CLI.
