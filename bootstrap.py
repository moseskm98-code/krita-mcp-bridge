"""One-time manual activation in Krita Scripter, without restarting Krita.

Run this ONE command inside Scripter:
import runpy; runpy.run_path('/absolute/path/to/krita-mcp-bridge/bootstrap.py')

The plugin is not installed by this script. It loads the reviewed local package
for this running Krita process only. Enabling the installed Python plugin later
provides the normal startup entry point.
"""
from pathlib import Path
import sys

plugin_root = Path(__file__).resolve().parent / 'krita_plugin'
if not (plugin_root / 'krita_mcp_bridge/bridge.py').is_file():
    raise RuntimeError('Reviewed Krita bridge files are missing')
if str(plugin_root) not in sys.path:
    sys.path.insert(0, str(plugin_root))
from krita_mcp_bridge.bridge import start_bridge
status = start_bridge()
print('Krita local bridge started:', status)
