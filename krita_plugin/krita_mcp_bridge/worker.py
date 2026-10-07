"""Optional kritarunner entry point; not activated or installed by this file.

Call run(args) only in an official kritarunner process after module-path setup.
It keeps Qt's main event loop alive for QTimer; no arbitrary commands accepted.
"""
from PyQt5.QtCore import QEventLoop
from PyQt5.QtWidgets import QApplication
from krita import Krita
from .bridge import start_bridge
from .validation import require

_event_loop = None


def run(args):
    global _event_loop
    require(isinstance(args, list) and not args, 'The bridge worker accepts no script arguments')
    qt_app = QApplication.instance()
    require(qt_app is not None, 'The worker requires the Krita application')
    qt_app.setQuitOnLastWindowClosed(False)
    app = Krita.instance()
    app.setBatchmode(True)
    status = start_bridge()
    print('Krita local headless bridge started:', status, flush=True)
    _event_loop = QEventLoop()
    _event_loop.exec_()
