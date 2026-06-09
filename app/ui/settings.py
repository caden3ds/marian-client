"""Settings dialog — the user enters their OWN credentials (nothing is hard-coded).

- Alpaca paper keys → saved to the OS keychain.
- Robinhood → OAuth connect (system browser), agentic account discovered + stored.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal, QThread, Qt
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QFormLayout, QLabel, QLineEdit, QPushButton, QFrame,
)

from app import credentials
from app.config import ROBINHOOD_MCP_URL


class _RhWorker(QObject):
    done = Signal(bool, str)

    def run(self):
        try:
            from app.adapters import mcp_client
            from app.adapters.robinhood_adapter import find_agentic_account

            data = mcp_client.authenticate(ROBINHOOD_MCP_URL)
            acct = find_agentic_account(data)
            if acct:
                credentials.set_rh_account(acct)
                self.done.emit(True, f"Connected ✓  Agentic account ••••{acct[-4:]}")
            else:
                self.done.emit(False, "No agentic account found — open one in the Robinhood app first.")
        except Exception as e:  # noqa: BLE001
            self.done.emit(False, f"Connection failed: {e}")


def _divider() -> QFrame:
    f = QFrame()
    f.setFrameShape(QFrame.HLine)
    return f


class SettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Settings — Connect your accounts")
        self.setMinimumWidth(460)
        v = QVBoxLayout(self)

        # --- Alpaca ---
        v.addWidget(QLabel("<b>Alpaca Paper</b> — your paper-trading keys from app.alpaca.markets"))
        form = QFormLayout()
        self.alp_key = QLineEdit()
        self.alp_sec = QLineEdit()
        self.alp_sec.setEchoMode(QLineEdit.Password)
        k, s = credentials.get_alpaca_keys()
        if k:
            self.alp_key.setText(k)
        if s:
            self.alp_sec.setText(s)
        form.addRow("Key ID", self.alp_key)
        form.addRow("Secret", self.alp_sec)
        v.addLayout(form)
        save = QPushButton("Save Alpaca keys")
        save.clicked.connect(self._save_alpaca)
        v.addWidget(save)
        self.alp_status = QLabel("Saved ✓" if credentials.alpaca_configured() else "")
        self.alp_status.setStyleSheet("color:#1a7f37;")
        v.addWidget(self.alp_status)

        v.addWidget(_divider())

        # --- Robinhood ---
        v.addWidget(QLabel("<b>Robinhood</b> — LIVE money. Connects via your browser (OAuth)."))
        self.rh_status = QLabel(self._rh_text())
        v.addWidget(self.rh_status)
        self.connect_btn = QPushButton("Connect Robinhood")
        self.connect_btn.clicked.connect(self._connect_rh)
        v.addWidget(self.connect_btn)

        v.addWidget(_divider())
        done = QPushButton("Done")
        done.clicked.connect(self.accept)
        v.addWidget(done)

    def _save_alpaca(self):
        credentials.set_alpaca_keys(self.alp_key.text(), self.alp_sec.text())
        ok = credentials.alpaca_configured()
        self.alp_status.setText("Saved ✓" if ok else "Both Key ID and Secret are required.")
        self.alp_status.setStyleSheet("color:#1a7f37;" if ok else "color:#cf222e;")

    def _rh_text(self):
        return ("Connected ✓  " + (credentials.get_rh_account() or "")) if credentials.robinhood_connected() else "Not connected."

    def _connect_rh(self):
        self.connect_btn.setEnabled(False)
        self.rh_status.setText("Opening browser… approve access in Robinhood, then return here.")
        self._thread = QThread()
        self._worker = _RhWorker()
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.done.connect(self._on_rh_done)
        self._thread.start()

    def _on_rh_done(self, ok: bool, msg: str):
        self.rh_status.setText(msg)
        self.rh_status.setStyleSheet("color:#1a7f37;" if ok else "color:#cf222e;")
        self.connect_btn.setEnabled(True)
        self._thread.quit()
