Review this diff excerpt from homelab-control (sandbox runner integration):

```diff
+def run_command_sandboxed(cmd, *, principal, image, allowed_hosts, audit_path):
+    runner = SandboxRunner(image=image, network_mode="slirp4netns-dns-allowlist")
+    return runner.run(cmd, allowed_hosts=allowed_hosts, audit_path=audit_path)
```

Context: author_agent routes `checks` jobs through Podman when `AUTHOR_AGENT_SANDBOX_CHECKS=1`.
Constraints: rootless Podman, default network none, DNS allowlist for forgejo only.

Provide: summary, risks, and 3 concrete review comments.
