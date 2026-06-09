"""Main dashboard window (PySide6).

Shows account/subscription status, the mode (PAPER/LIVE) and execution tier, strategy
toggles, a live signals + paper/live execution preview, a kill switch, an order blotter,
and realized P&L. Signals arrive on a background thread and are marshalled to the UI via a
Qt StreamBridge.
"""

from __future__ import annotations

from decimal import Decimal

from PySide6.QtCore import Qt, QObject, Signal, QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app import __version__
from app.config import (
    ClientConfig, PAPER, LIVE, MANUAL, DELAYED, AUTO,
    BROKER_SIM, BROKER_ALPACA_PAPER, BROKER_RH_LIVE,
)

BROKER_OPTIONS = [
    ("Alpaca Paper", BROKER_ALPACA_PAPER),
    ("Robinhood (LIVE $)", BROKER_RH_LIVE),
]
from app.signals import SignalStream
from app.adapters.robinhood_adapter import PaperBroker
from app.execution.engine import ExecutionEngine

STRATEGY_LABELS = {
    "gap_and_go": "Gap & Go",
    "orb": "Opening Range Breakout",
    "vwap_breakout": "VWAP Breakout",
    "ema_cross": "9/21 EMA Cross",
    "mean_reversion": "Mean Reversion",
}

PHASE_GROUPS = {
    "Phase 1 — Opening Chaos (9:30–10:00)": ["gap_and_go", "orb"],
    "Phase 2 — Mid-Day Trend (10:00–15:30)": ["vwap_breakout", "ema_cross"],
    "Phase 3 — Closing Snap (15:30–16:00)": ["mean_reversion"],
}

TIER_LABELS = {MANUAL: "Manual approve", DELAYED: "Delayed (15s cancel)", AUTO: "Full auto"}


class StreamBridge(QObject):
    new_signal = Signal(dict)


class Dashboard(QMainWindow):
    def __init__(self, config: ClientConfig | None = None, project_id: str | None = None):
        super().__init__()
        self.config = config or ClientConfig()
        self.project_id = project_id
        self.session = None
        self.stream: SignalStream | None = None
        self.engine: ExecutionEngine | None = None
        self.bridge = StreamBridge()
        self.bridge.new_signal.connect(self._handle_signal)
        self.setWindowTitle(f"Hardspace Finance — Thin Client v{__version__}")
        self.resize(1100, 720)

        root = QWidget()
        layout = QVBoxLayout(root)
        layout.addWidget(self._build_header())
        body = QHBoxLayout()
        body.addWidget(self._build_strategy_panel(), 1)
        body.addWidget(self._build_signal_table(), 3)
        layout.addLayout(body)
        layout.addWidget(self._build_blotter())
        self.setCentralWidget(root)

    # ---- session wiring ------------------------------------------------------
    def attach_session(self, session, stream: SignalStream | None = None, refresher=None) -> None:
        self.session = session
        self.account_label.setText(session.email or session.uid)
        self._rebuild_engine()

        if session.subscription_active:
            self._set_subscription(True)
            self.stream = stream or SignalStream(session, self.project_id, refresher=refresher)
            self.stream.start(self.bridge.new_signal.emit)
            self.status_note.setText("Live — streaming today's signals.")
        else:
            self._set_subscription(False)
            self.status_note.setText("No active subscription — signals are hidden.")

    def _rebuild_engine(self) -> None:
        broker, account_number, balance_text = self._make_broker()
        self.balance_label.setText(balance_text)
        if broker is None:
            self.engine = None  # not configured → signals still show, but won't execute
            return
        self.engine = ExecutionEngine(broker, self.config.risk, account_number, tier=self.config.tier())
        self._refresh_pnl()

    def _open_settings(self) -> None:
        from app.ui.settings import SettingsDialog

        SettingsDialog(self).exec()
        if self.session:
            self._rebuild_engine()  # credentials may have changed

    def _set_mode(self, mode: str) -> None:
        self.config.mode = mode
        is_paper = mode == PAPER
        self.mode_label.setText("PAPER MODE" if is_paper else "LIVE MODE")
        color = "#1a7f37" if is_paper else "#cf222e"
        self.mode_label.setStyleSheet(
            f"color: white; background: {color}; padding: 4px 12px; border-radius: 6px; font-weight: 700;"
        )

    def _on_broker_changed(self, _idx: int) -> None:
        self.config.broker = self.broker_combo.currentData()
        # Robinhood = LIVE money; everything else = paper. Reset tier to the safe default.
        self._set_mode(LIVE if self.config.broker == BROKER_RH_LIVE else PAPER)
        self.config.execution_tier = None
        self.tier_combo.setCurrentIndex([MANUAL, DELAYED, AUTO].index(self.config.tier()))
        self._update_tier_warning()
        if self.session:
            self._rebuild_engine()

    def _make_broker(self):
        """Build the execution backend from user-entered credentials. Returns (None, None, msg)
        when the selected broker isn't configured yet (the user is prompted to open Settings)."""
        from app import credentials

        if self.config.broker == BROKER_RH_LIVE:
            if not credentials.robinhood_connected():
                return None, None, "Robinhood not connected — open ⚙ Settings."
            from app.adapters.robinhood_adapter import LiveBroker
            from app.config import ROBINHOOD_MCP_URL

            broker = LiveBroker(ROBINHOOD_MCP_URL)
            acct = credentials.get_rh_account()
            try:
                bp = broker.get_buying_power(acct)
                return broker, acct, f"Robinhood LIVE buying power: ${bp}"
            except Exception as e:
                return broker, acct, f"Robinhood connected (buying power unavailable: {e})"

        # default: Alpaca paper
        if not credentials.alpaca_configured():
            return None, None, "Alpaca keys not set — open ⚙ Settings."
        try:
            from app.adapters.alpaca_broker import AlpacaPaperBroker

            broker = AlpacaPaperBroker()
            bp = broker.get_buying_power("paper")
            return broker, "alpaca-paper", f"Alpaca paper buying power: ${bp}"
        except Exception as e:
            return None, None, f"Alpaca error: {e} — check ⚙ Settings."

    # ---- header --------------------------------------------------------------
    def _build_header(self) -> QWidget:
        box = QWidget()
        outer = QVBoxLayout(box)
        row = QHBoxLayout()
        title = QLabel("All-Weather Algorithmic Engine")
        title.setStyleSheet("font-size: 18px; font-weight: 600;")
        row.addWidget(title)
        row.addStretch()

        self.account_label = QLabel("— not signed in —")
        self.account_label.setStyleSheet("color: #555;")
        row.addWidget(self.account_label)

        self.sub_label = QLabel("INACTIVE")
        self._set_subscription_placeholder()
        row.addWidget(self.sub_label)

        is_paper = self.config.mode == PAPER
        self.mode_label = QLabel("PAPER MODE" if is_paper else "LIVE MODE")
        color = "#1a7f37" if is_paper else "#cf222e"
        self.mode_label.setStyleSheet(
            f"color: white; background: {color}; padding: 4px 12px; border-radius: 6px; font-weight: 700;"
        )
        row.addWidget(self.mode_label)
        outer.addLayout(row)

        controls = QHBoxLayout()
        controls.addWidget(QLabel("Broker:"))
        self.broker_combo = QComboBox()
        for label, val in BROKER_OPTIONS:
            self.broker_combo.addItem(label, val)
        cur = [v for _, v in BROKER_OPTIONS].index(self.config.broker) if self.config.broker in [v for _, v in BROKER_OPTIONS] else 0
        self.broker_combo.setCurrentIndex(cur)
        self.broker_combo.currentIndexChanged.connect(self._on_broker_changed)
        controls.addWidget(self.broker_combo)

        self.settings_btn = QPushButton("⚙ Settings")
        self.settings_btn.clicked.connect(self._open_settings)
        controls.addWidget(self.settings_btn)

        controls.addWidget(QLabel("Execution:"))
        self.tier_combo = QComboBox()
        for t in (MANUAL, DELAYED, AUTO):
            self.tier_combo.addItem(TIER_LABELS[t], t)
        self.tier_combo.setCurrentIndex([MANUAL, DELAYED, AUTO].index(self.config.tier()))
        self.tier_combo.currentIndexChanged.connect(self._on_tier_changed)
        controls.addWidget(self.tier_combo)
        self.tier_warning = QLabel("")
        self.tier_warning.setStyleSheet("color: #cf222e; font-size: 11px;")
        controls.addWidget(self.tier_warning)
        controls.addStretch()

        self.pnl_label = QLabel("")
        self.pnl_label.setStyleSheet("font-weight: 600;")
        controls.addWidget(self.pnl_label)

        self.kill_btn = QPushButton("■ KILL SWITCH")
        self.kill_btn.setStyleSheet("background: #cf222e; color: white; font-weight: 700; padding: 4px 10px;")
        self.kill_btn.clicked.connect(self._toggle_kill)
        controls.addWidget(self.kill_btn)
        outer.addLayout(controls)

        sub = QHBoxLayout()
        self.status_note = QLabel("Sign in to begin.")
        self.status_note.setStyleSheet("color: #777;")
        sub.addWidget(self.status_note)
        sub.addStretch()
        self.balance_label = QLabel("")
        self.balance_label.setStyleSheet("color: #555; font-weight: 600;")
        sub.addWidget(self.balance_label)
        outer.addLayout(sub)

        self._update_tier_warning()
        return box

    def _set_subscription_placeholder(self) -> None:
        self.sub_label.setStyleSheet(
            "color: white; background: #888; padding: 3px 10px; border-radius: 6px; font-weight: 700;"
        )

    def _set_subscription(self, active: bool) -> None:
        self.sub_label.setText("SUBSCRIBED" if active else "INACTIVE")
        color = "#1a7f37" if active else "#cf222e"
        self.sub_label.setStyleSheet(
            f"color: white; background: {color}; padding: 3px 10px; border-radius: 6px; font-weight: 700;"
        )

    def _on_tier_changed(self, _idx: int) -> None:
        self.config.execution_tier = self.tier_combo.currentData()
        if self.engine:
            self.engine.tier = self.config.execution_tier
        self._update_tier_warning()

    def _update_tier_warning(self) -> None:
        tier = self.config.tier()
        if self.config.mode != PAPER and tier in (DELAYED, AUTO):
            self.tier_warning.setText(
                "⚠ Auto-execution of live orders resembles conduct the SEC found to forfeit the "
                "publisher's exclusion (Weiss Research, IA-2525). Use at your own risk."
            )
        else:
            self.tier_warning.setText("")

    def _toggle_kill(self) -> None:
        if not self.engine:
            return
        if self.engine.killed:
            self.engine.resume()
            self.kill_btn.setText("■ KILL SWITCH")
            self.kill_btn.setStyleSheet("background: #cf222e; color: white; font-weight: 700; padding: 4px 10px;")
        else:
            self.engine.kill()
            self.kill_btn.setText("▶ RESUME (halted)")
            self.kill_btn.setStyleSheet("background: #555; color: white; font-weight: 700; padding: 4px 10px;")

    # ---- strategy toggles ----------------------------------------------------
    def _build_strategy_panel(self) -> QWidget:
        panel = QGroupBox("Strategies")
        outer = QVBoxLayout(panel)
        self.strategy_checks: dict[str, QCheckBox] = {}
        for group_name, keys in PHASE_GROUPS.items():
            group = QGroupBox(group_name)
            gl = QVBoxLayout(group)
            for key in keys:
                cb = QCheckBox(STRATEGY_LABELS[key])
                cb.setChecked(key in self.config.enabled_strategies)
                cb.toggled.connect(lambda on, k=key: self._on_toggle(k, on))
                self.strategy_checks[key] = cb
                gl.addWidget(cb)
            outer.addWidget(group)
        outer.addStretch()
        return panel

    def _on_toggle(self, key: str, on: bool) -> None:
        if on:
            self.config.enabled_strategies.add(key)
        else:
            self.config.enabled_strategies.discard(key)

    # ---- signals + preview table --------------------------------------------
    def _build_signal_table(self) -> QWidget:
        panel = QGroupBox("Signals & Execution Preview")
        v = QVBoxLayout(panel)
        self.table = QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels(
            ["Time", "Symbol", "Side", "Strategy", "Order", "Shares", "Est $", "Plan"]
        )
        self.table.horizontalHeader().setStretchLastSection(True)
        v.addWidget(self.table)
        return panel

    def _build_blotter(self) -> QWidget:
        panel = QGroupBox("Order Blotter")
        v = QVBoxLayout(panel)
        self.blotter_table = QTableWidget(0, 6)
        self.blotter_table.setHorizontalHeaderLabels(
            ["Symbol", "Strategy", "Side", "Qty", "Price", "Status"]
        )
        self.blotter_table.horizontalHeader().setStretchLastSection(True)
        self.blotter_table.setMaximumHeight(180)
        v.addWidget(self.blotter_table)
        return panel

    def _handle_signal(self, signal: dict) -> None:
        if signal.get("strategy") not in self.config.enabled_strategies:
            return
        if not self.engine:
            self.add_preview_row(signal, None)  # show it; can't trade until a broker is set up
            return
        plan = self.engine.preview(signal)
        row = self.add_preview_row(signal, plan)

        if self.engine.killed:
            self._set_status(row, "HALTED (kill switch)", Qt.gray)
            return
        if plan.decision == "skip":
            return
        if plan.decision == "exit":
            self._execute(plan, row)
            return

        tier = self.config.tier()
        if tier == AUTO:
            self._execute(plan, row)
        elif tier == MANUAL:
            self._add_actions(row, plan, delayed=False)
        elif tier == DELAYED:
            self._add_actions(row, plan, delayed=True)

    def _add_actions(self, row: int, plan, delayed: bool) -> None:
        widget = QWidget()
        hl = QHBoxLayout(widget)
        hl.setContentsMargins(2, 0, 2, 0)
        approve = QPushButton("Approve")
        cancel = QPushButton("Cancel")
        hl.addWidget(approve)
        hl.addWidget(cancel)
        self.table.setCellWidget(row, 7, widget)

        # Surface the broker's pre-trade review (Robinhood requires the market-data disclosure
        # to be shown verbatim; also surface any pre-trade alert) before the user confirms.
        review = getattr(plan, "review", None)
        if review is not None:
            tip = review.detail or ""
            if getattr(review, "alert", None):
                tip += f"\n⚠ ALERT: {review.alert}"
            if getattr(review, "disclosure", None):
                tip += f"\n{review.disclosure}"
            approve.setToolTip(tip)
            if getattr(review, "alert", None):
                self._set_status(row, f"REVIEW ALERT: {review.alert}", Qt.darkYellow)

        state = {"done": False}

        def do_approve():
            if state["done"]:
                return
            state["done"] = True
            self.table.removeCellWidget(row, 7)
            self._execute(plan, row)

        def do_cancel():
            if state["done"]:
                return
            state["done"] = True
            self.table.removeCellWidget(row, 7)
            self._set_status(row, "CANCELLED", Qt.gray)

        approve.clicked.connect(do_approve)
        cancel.clicked.connect(do_cancel)

        if delayed:
            cancel.setText(f"Cancel (auto {self.config.delayed_seconds}s)")
            QTimer.singleShot(self.config.delayed_seconds * 1000, do_approve)

    def _execute(self, plan, row: int) -> None:
        result = self.engine.execute(plan)
        if result is None:
            self._set_status(row, "HALTED", Qt.gray)
        elif result.accepted:
            tag = "FILLED" if result.paper else "LIVE FILLED"
            self._set_status(row, tag, Qt.darkGreen)
        else:
            self._set_status(row, "REJECTED", Qt.red)
        self._refresh_blotter()
        self._refresh_pnl()

    def add_preview_row(self, signal: dict, plan) -> int:
        r = self.table.rowCount()
        self.table.insertRow(r)
        order = signal.get("order", {})
        if plan is None:
            shares, est, status, otype = "", "", "—", order.get("type", "")
        elif plan.decision == "place":
            shares = (
                f"{plan.shares:.4f}".rstrip("0").rstrip(".") if plan.fractional else f"{int(plan.shares)}"
            )
            est = f"{plan.est_cost:.2f}" if plan.est_cost is not None else ""
            otype = plan.order.type if plan.order else order.get("type", "")
            status = "PLACE (frac)" if plan.fractional else "PENDING"
        elif plan.decision == "exit":
            shares, est, otype, status = "", "", "market", "EXIT"
        else:
            shares, est, otype, status = "0", "", order.get("type", ""), f"SKIP — {plan.reason}"

        cells = [
            signal.get("issued_at", ""), signal.get("symbol", ""), signal.get("side", ""),
            signal.get("strategy", ""), otype, shares, est, status,
        ]
        for c, text in enumerate(cells):
            self.table.setItem(r, c, QTableWidgetItem(str(text)))
        return r

    def _set_status(self, row: int, text: str, color=None) -> None:
        item = QTableWidgetItem(text)
        if color is not None:
            item.setForeground(color)
        self.table.setItem(row, 7, item)

    def _refresh_blotter(self) -> None:
        rows = self.engine.blotter
        self.blotter_table.setRowCount(0)
        for rec in rows[-100:]:
            r = self.blotter_table.rowCount()
            self.blotter_table.insertRow(r)
            cells = [rec["symbol"], rec["strategy"], rec["side"], rec["qty"], rec["price"], rec["status"]]
            for c, text in enumerate(cells):
                self.blotter_table.setItem(r, c, QTableWidgetItem(str(text)))

    def _refresh_pnl(self) -> None:
        if not self.engine:
            return
        self.pnl_label.setText(
            f"Realized P&L: ${self.engine.realized_pnl:.2f}  ·  Open: {len(self.engine.positions)}"
        )

    def closeEvent(self, event):  # noqa: N802
        if self.stream:
            self.stream.stop()
        super().closeEvent(event)
