# Krita Python bridge / Python-мост Krita

For a running Krita, manually execute this in Scripter with your absolute repository path:

```python
import runpy; runpy.run_path('/absolute/path/to/krita-mcp-bridge/bootstrap.py')
```

Bootstrap does not install the plugin or change preferences. Activation lasts for that process. The [main README](../README.md) also describes a separate macOS worker.

For persistent activation, copy `krita_mcp_bridge/` and `krita_mcp_bridge.desktop` into the `pykrita` subfolder of Krita's user resource folder. Locate it with Krita's **Open Resource Folder** command. Enable **Krita MCP Bridge** in Python Plugin Manager. Restart Krita yourself after saving documents; an agent must not restart a user's working GUI Krita.

В работающей Krita вручную выполните строку выше в Scripter. Bootstrap не устанавливает плагин и не меняет настройки. Для постоянной активации скопируйте пакет и `.desktop` в `pykrita` пользовательских ресурсов, включите плагин в Python Plugin Manager и самостоятельно перезапустите Krita после сохранения документов.

[Official installation guide](https://docs.krita.org/en/user_manual/python_scripting/krita_python_plugin_howto.html).

The authenticated HTTP server binds to `127.0.0.1`. Token files use `0600`, runtime folders `0700`. HTTP threads enqueue named commands; main-thread QTimer executes them. Only bridge-owned copies are mutable. Outputs are staged and published exclusively to new paths.

[Operations / Операции](../docs/OPERATIONS.md) · [Known issues / Ошибки](../docs/KNOWN_ISSUES.md).
