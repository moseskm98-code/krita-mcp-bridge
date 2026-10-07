# Node MCP server

```sh
npm ci
node server/index.mjs
node server/call.mjs list
node server/call.mjs state
node server/call.mjs open_copy '{"path":"/absolute/source.png"}'
node server/call.mjs mask_set @/absolute/arguments.json
```

`index.mjs` reserves stdout for MCP. The Python bridge must already be active. CLI calls perform actual MCP initialize/listTools/callTool, not direct HTTP.

The default connection file is `~/.local/share/krita-mcp-bridge/connection.json`. `KRITA_MCP_CONNECTION_FILE` selects another backend for tests. `KRITA_MCP_TIMEOUT_MS` sets 1–300000 ms, default 120000 ms. Tokens are not CLI arguments.

Сервер использует stdout только для MCP. Python-мост должен быть активен. CLI выполняет настоящий MCP. После ошибки записи проверьте состояние и файлы до повтора.

[Known recovery limits / Ограничения восстановления](../docs/KNOWN_ISSUES.md).
