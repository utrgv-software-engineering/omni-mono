"""Network configuration for omni-code.

Provides read/write access to ~/.config/omni_code/network.json,
used by the sandbox CLI to configure kernel-level network isolation
(iptables allowlist inside the container).

Network enforcement is handled by:
- Sandbox mode: iptables rules set by the container entrypoint
  (OMNI_SANDBOX_NETWORK_ALLOWLIST env var)
- Launcher: passes allowlist from platform policy to the sandbox
"""
