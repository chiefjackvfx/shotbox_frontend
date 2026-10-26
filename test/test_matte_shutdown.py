"""Busy tools stop frontend shutdown before any host resources are torn down."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from types import SimpleNamespace
from unittest import mock
from PyQt6.QtWidgets import QApplication
import main


def test_busy_matte_keeps_shotbox_open():
    app = QApplication.instance() or QApplication([])
    toolbox = SimpleNamespace(has_busy_tools=lambda: True, close_tools=mock.Mock())
    host = SimpleNamespace(page_toolbox=toolbox, page_settings=None)
    event = mock.Mock()
    with mock.patch.object(main.QtWidgets.QMessageBox, "information") as message:
        main.MainWindow.closeEvent(host, event)
    event.ignore.assert_called_once()
    toolbox.close_tools.assert_not_called()
    assert "Finish or cancel" in message.call_args.args[2]
    assert QApplication.instance() is app


def test_runtime_setup_keeps_shotbox_open():
    app = QApplication.instance() or QApplication([])
    host = SimpleNamespace(page_toolbox=None, page_settings=SimpleNamespace(
        matte_settings=SimpleNamespace(is_busy=lambda: True)))
    event = mock.Mock()
    with mock.patch.object(main.QtWidgets.QMessageBox, "information") as message:
        main.MainWindow.closeEvent(host, event)
    event.ignore.assert_called_once()
    assert "preparation" in message.call_args.args[2]
    assert QApplication.instance() is app
