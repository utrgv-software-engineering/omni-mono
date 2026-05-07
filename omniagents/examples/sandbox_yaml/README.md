# Sandbox YAML Example

This example builds a `SandboxAgent` (SDK 0.14+) backed by the bundled
`unix_local` sandbox client. The client materializes the manifest into a
fresh temporary workspace directory on the host — no Docker or third-party
provider required.

## Usage

```bash
python -m omniagents.cli run -c examples/sandbox_yaml/agent.yml
```

## Switching providers

To run the same configuration against a different sandbox provider, change
`sandbox.client.type` and install the matching extra:

| Provider     | YAML `client.type` | Extra                          |
|--------------|--------------------|--------------------------------|
| Local Unix   | `unix_local`       | (built in)                     |
| Docker       | `docker`           | `omniagents[sandbox-docker]`   |
| E2B          | `e2b`              | `omniagents[sandbox-e2b]`      |
| Modal        | `modal`            | `omniagents[sandbox-modal]`    |
| Runloop      | `runloop`          | `omniagents[sandbox-runloop]`  |
| Daytona      | `daytona`          | `omniagents[sandbox-daytona]`  |
| Blaxel       | `blaxel`           | `omniagents[sandbox-blaxel]`   |
| Vercel       | `vercel`           | `omniagents[sandbox-vercel]`   |
| Cloudflare   | `cloudflare`       | `omniagents[sandbox-cloudflare]` |
