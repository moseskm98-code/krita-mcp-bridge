# Development rules

- Keep MCP stdout reserved for protocol messages.
- Use named operations and the official Krita Python API on the main Qt thread.
- Do not add GUI automation, screenshots, or arbitrary code execution endpoints.
- Edit bridge-owned document copies. Reject existing output paths.
- Keep runtime credentials out of source control, responses, and logs.
- Do not close or restart a user's running Krita during development.
- Transport tests do not prove native Krita behavior. Describe test evidence accurately.
- Preserved source coordinates do not certify final image geometry or quality.
