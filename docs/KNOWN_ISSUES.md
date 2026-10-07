# Known issues / Известные ошибки

Experimental initial snapshot. Public packaging does not fix these independently confirmed core issues.

Экспериментальная начальная версия. Подготовка публичного репозитория не исправляет подтверждённые ошибки ядра.

| Priority | English | Русский |
| --- | --- | --- |
| P1 | A client-cancelled MCP call may remain queued and reach Krita later. | Отменённая клиентом MCP-команда может позже уйти в Krita. |
| P2 | Connection loss after an accepted request can return `BRIDGE_UNREACHABLE` without `outcomeUnknown`. | При обрыве ответа после принятия команды отсутствует `outcomeUnknown`. |
| P2 | Non-ASCII Authorization can raise uncaught `TypeError` and disconnect that HTTP request. No authorization bypass observed. | Не-ASCII Authorization может вызвать `TypeError` и разрыв запроса. Обход авторизации не наблюдался. |
| P3 | Defensive redaction can retain a token in a JSON key returned by a faulty backend. No real runtime-token leak observed. | Защитная очистка может пропустить токен в ключе JSON ошибочного backend. Утечка настоящего токена не наблюдалась. |

Relevant files: `server/index.mjs`, `server/bridge.mjs`, `krita_plugin/krita_mcp_bridge/bridge.py`.

**Inspect state, layer trees, and output files before retrying mutations after cancellation, timeout, or connection loss.** A client timeout does not prove that a queued command was cancelled before execution.

**После отмены, тайм-аута или потери связи проверьте состояние, слои и выходные файлы до повтора правки.** Тайм-аут клиента не доказывает отмену команды до исполнения.
