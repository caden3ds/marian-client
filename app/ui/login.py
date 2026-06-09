"""Login dialog — email/password sign-in or account creation against Firebase Auth."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)

from app.auth import AuthClient, Session
from app.firebase_rest import AuthError


class LoginDialog(QDialog):
    def __init__(self, auth: AuthClient, parent=None):
        super().__init__(parent)
        self.auth = auth
        self.session: Session | None = None
        self.setWindowTitle("Hardspace Finance — Sign In")
        self.setMinimumWidth(360)

        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.email = QLineEdit()
        self.email.setPlaceholderText("you@example.com")
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.Password)
        form.addRow("Email", self.email)
        form.addRow("Password", self.password)
        layout.addLayout(form)

        self.error = QLabel("")
        self.error.setStyleSheet("color: #cf222e;")
        self.error.setWordWrap(True)
        layout.addWidget(self.error)

        buttons = QDialogButtonBox()
        self.signin_btn = QPushButton("Sign In")
        self.signup_btn = QPushButton("Create Account")
        buttons.addButton(self.signin_btn, QDialogButtonBox.AcceptRole)
        buttons.addButton(self.signup_btn, QDialogButtonBox.ActionRole)
        buttons.addButton(QDialogButtonBox.Cancel)
        buttons.rejected.connect(self.reject)
        self.signin_btn.clicked.connect(self._sign_in)
        self.signup_btn.clicked.connect(self._sign_up)
        layout.addWidget(buttons)

    def _attempt(self, fn) -> None:
        self.error.setText("")
        try:
            self.session = fn(self.email.text().strip(), self.password.text())
            self.accept()
        except AuthError as e:
            self.error.setText(str(e))
        except Exception as e:  # network etc.
            self.error.setText(f"Connection error: {e}")

    def _sign_in(self) -> None:
        self._attempt(self.auth.sign_in)

    def _sign_up(self) -> None:
        self._attempt(self.auth.sign_up)
