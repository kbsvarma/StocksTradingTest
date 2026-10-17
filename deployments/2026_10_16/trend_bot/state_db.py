"""Durable broker-event journal and operational position state machine.

SQLite is the commit barrier for every broker-driven position transition.
Broker executions may arrive before the strategy applies them, so raw capture
(`executions`) is deliberately separate from application
(`applied_executions`).  A transition atomically:

* records the broker event and per-execution provenance;
* records the closed-trade accounting, when applicable;
* marks executions/order cumulative quantity as applied; and
* advances the canonical operational position projection.

``state.json`` and CSV files are human-readable projections.  On restart the
bot hydrates them from ``position_fsm`` before reconciling with the broker.
"""
from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
  run_id INTEGER PRIMARY KEY AUTOINCREMENT,
  started TEXT NOT NULL,
  run_kind TEXT NOT NULL DEFAULT 'runtime',
  venue TEXT,
  account TEXT,
  code_commit TEXT,
  config_sha256 TEXT,
  tree_dirty INTEGER
);
CREATE TABLE IF NOT EXISTS runtime_identity (
  singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
  venue TEXT NOT NULL,
  account TEXT NOT NULL,
  generation TEXT NOT NULL,
  bound_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS order_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id INTEGER,
  ts TEXT NOT NULL,
  account TEXT,
  sym TEXT NOT NULL,
  kind TEXT NOT NULL,
  event TEXT NOT NULL,
  event_key TEXT,
  order_id INTEGER,
  perm_id INTEGER,
  qty REAL,
  px REAL,
  note TEXT
);
CREATE TABLE IF NOT EXISTS executions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id INTEGER,
  ts TEXT NOT NULL,
  account TEXT,
  sym TEXT NOT NULL,
  side TEXT,
  qty REAL NOT NULL,
  px REAL NOT NULL,
  local_symbol TEXT,
  con_id INTEGER,
  commission REAL,
  exec_id TEXT,
  exec_time TEXT,
  order_ref TEXT,
  order_id INTEGER,
  perm_id INTEGER,
  transition_key TEXT
);
CREATE TABLE IF NOT EXISTS commission_reports (
  account TEXT NOT NULL,
  exec_id TEXT NOT NULL,
  commission REAL NOT NULL,
  currency TEXT,
  realized_pnl REAL,
  received_at TEXT NOT NULL,
  PRIMARY KEY (account, exec_id)
);
CREATE TABLE IF NOT EXISTS broker_events (
  event_key TEXT PRIMARY KEY,
  run_id INTEGER,
  created TEXT NOT NULL,
  updated TEXT NOT NULL,
  account TEXT,
  venue TEXT,
  sym TEXT NOT NULL,
  event_type TEXT NOT NULL,
  status TEXT NOT NULL,
  order_ref TEXT,
  order_id INTEGER,
  perm_id INTEGER,
  payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS applied_executions (
  account TEXT NOT NULL,
  exec_id TEXT NOT NULL,
  event_key TEXT NOT NULL,
  applied_at TEXT NOT NULL,
  PRIMARY KEY (account, exec_id)
);
CREATE TABLE IF NOT EXISTS order_progress (
  account TEXT NOT NULL,
  order_key TEXT NOT NULL,
  applied_qty REAL NOT NULL,
  event_key TEXT NOT NULL,
  updated TEXT NOT NULL,
  PRIMARY KEY (account, order_key)
);
CREATE TABLE IF NOT EXISTS position_fsm (
  account TEXT NOT NULL,
  venue TEXT NOT NULL,
  sym TEXT NOT NULL,
  fsm_state TEXT NOT NULL,
  qty REAL NOT NULL,
  con_id INTEGER,
  stop_order_id INTEGER,
  last_event_key TEXT NOT NULL,
  version INTEGER NOT NULL,
  payload TEXT NOT NULL,
  updated TEXT NOT NULL,
  PRIMARY KEY (account, venue, sym)
);
CREATE TABLE IF NOT EXISTS trades (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id INTEGER,
  event_key TEXT,
  exit_time TEXT NOT NULL,
  book TEXT, sym TEXT NOT NULL, qty REAL NOT NULL,
  entry_time TEXT, entry_px REAL, exit_px REAL,
  reason TEXT,
  pnl_points REAL,
  pnl_gross REAL,
  commission REAL,
  manual_commission REAL DEFAULT 0,
  pnl_net REAL,
  entry_weight REAL,
  local_symbol TEXT
);
CREATE TABLE IF NOT EXISTS trade_executions (
  trade_event_key TEXT NOT NULL,
  account TEXT NOT NULL,
  exec_id TEXT NOT NULL,
  leg TEXT NOT NULL CHECK (leg IN ('ENTRY', 'EXIT')),
  PRIMARY KEY (trade_event_key, account, exec_id)
);
CREATE TABLE IF NOT EXISTS corrections (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,
  kind TEXT NOT NULL,
  ref TEXT,
  delta_usd REAL NOT NULL,
  note TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS position_snapshots (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,
  account TEXT,
  snapshot TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS readiness (
  component TEXT PRIMARY KEY,
  status TEXT NOT NULL,
  detail TEXT,
  updated TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS soak_epochs (
  epoch_id INTEGER PRIMARY KEY AUTOINCREMENT,
  started TEXT NOT NULL,
  account TEXT NOT NULL,
  venue TEXT NOT NULL,
  generation TEXT NOT NULL,
  code_commit TEXT NOT NULL,
  config_sha256 TEXT NOT NULL,
  note TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS soak_invalidations (
  invalidation_id INTEGER PRIMARY KEY AUTOINCREMENT,
  epoch_id INTEGER NOT NULL UNIQUE,
  invalidated TEXT NOT NULL,
  reason TEXT NOT NULL,
  FOREIGN KEY (epoch_id) REFERENCES soak_epochs(epoch_id)
);
"""


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _json(value) -> str:
    return json.dumps(value, sort_keys=True, default=str)


def release_provenance() -> dict:
    """Read the exact source identity without mutating operational state."""
    base = Path(__file__).resolve().parent
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=base,
            capture_output=True, text=True, timeout=5,
            check=True).stdout.strip()
        status = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=all"],
            cwd=base, capture_output=True, text=True, timeout=5,
            check=True).stdout.strip()
        tree_dirty = bool(status)
    except Exception:
        commit, tree_dirty = None, True
    try:
        cfg_sha = hashlib.sha256(
            (base / "config.yaml").read_bytes()).hexdigest()[:16]
    except Exception:
        cfg_sha = None
    return {"code_commit": commit, "config_sha256": cfg_sha,
            "tree_dirty": tree_dirty}


class StateDB:
    def __init__(self, state_dir: Path, venue: str = "?", *,
                 run_kind: str = "runtime"):
        self.path = Path(state_dir) / "trendbot.db"
        self.venue = venue
        self.account: str | None = None
        self.con = sqlite3.connect(self.path)
        try:
            mode = self.con.execute("PRAGMA journal_mode=WAL").fetchone()[0]
            if str(mode).lower() != "wal":
                raise RuntimeError(
                    f"SQLite WAL unavailable for {self.path}: {mode}")
            self.con.execute("PRAGMA synchronous=FULL")
            if self.con.execute("PRAGMA synchronous").fetchone()[0] != 2:
                raise RuntimeError(
                    "SQLite synchronous=FULL could not be enforced")
            self.con.execute("PRAGMA foreign_keys=ON")
            self.con.execute("PRAGMA busy_timeout=5000")
            self.con.executescript(SCHEMA)
            self._migrate_legacy_schema()
            self._create_indexes_and_views()
            if not run_kind:
                raise ValueError("run_kind is required")
            self.run_kind = run_kind
            self.run_id = self._start_run(venue)
        except Exception:
            self.con.close()
            raise

    def _columns(self, table: str) -> set[str]:
        return {r[1] for r in self.con.execute(f"PRAGMA table_info({table})")}

    def _add_column(self, table: str, definition: str):
        name = definition.split()[0]
        if name not in self._columns(table):
            self.con.execute(f"ALTER TABLE {table} ADD COLUMN {definition}")

    def _migrate_legacy_schema(self):
        """Additive, transactional migration from the pre-FSM journal."""
        with self.con:
            for definition in (
                    "account TEXT", "event_key TEXT"):
                self._add_column("order_events", definition)
            for definition in (
                    "account TEXT", "exec_id TEXT", "exec_time TEXT",
                    "order_ref TEXT", "order_id INTEGER", "perm_id INTEGER",
                    "transition_key TEXT"):
                self._add_column("executions", definition)
            self._add_column("trades", "event_key TEXT")
            self._add_column("trades", "manual_commission REAL DEFAULT 0")
            self._add_column("position_snapshots", "account TEXT")
            self._add_column("runs", "account TEXT")
            self._add_column("runs", "tree_dirty INTEGER")
            self._add_column(
                "runs", "run_kind TEXT NOT NULL DEFAULT 'runtime'")

    def _create_indexes_and_views(self):
        with self.con:
            # The old global exec-id index is unsafe for linked accounts.
            self.con.execute("DROP INDEX IF EXISTS ux_exec_id")
            self.con.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS ux_execution_account_exec "
                "ON executions(account, exec_id) WHERE exec_id IS NOT NULL")
            self.con.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS ux_trade_event "
                "ON trades(event_key) WHERE event_key IS NOT NULL")
            # A broker execution belongs to one closed-trade allocation only.
            # Never "repair" legacy conflicts by deleting one arbitrarily:
            # that would rewrite accounting provenance during startup.  A
            # conflicting migration requires explicit forensic correction.
            conflicts = self.con.execute(
                "SELECT account,exec_id,COUNT(*) FROM trade_executions "
                "GROUP BY account,exec_id HAVING COUNT(*)>1 LIMIT 10"
            ).fetchall()
            if conflicts:
                raise sqlite3.IntegrityError(
                    "legacy trade execution mappings conflict; refusing "
                    f"lossy migration: {conflicts}")
            self.con.execute("DROP INDEX IF EXISTS ux_trade_execution_leg")
            self.con.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS ux_trade_execution_once "
                "ON trade_executions(account,exec_id)")
            # Corrections are append-only, but retrying the same referenced
            # correction must not move P&L twice. A reversal uses a new kind
            # and an explicit audit note.
            self.con.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS ux_correction_kind_ref "
                "ON corrections(kind,ref) WHERE ref IS NOT NULL")
            self.con.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS ux_order_event_key_event "
                "ON order_events(event_key, event) "
                "WHERE event_key IS NOT NULL")
            self.con.execute("DROP VIEW IF EXISTS v_net_pnl")
            self.con.execute("DROP VIEW IF EXISTS v_trade_net")
            # For transitioned trades, fee truth comes from the mapped broker
            # executions and updates automatically when a late commission
            # report arrives.  Migrated historical trades retain their stored
            # commission value.
            self.con.execute("""
                CREATE VIEW v_trade_net AS
                SELECT t.*,
                  t.pnl_gross -
                    CASE WHEN EXISTS (
                      SELECT 1 FROM trade_executions te
                      WHERE te.trade_event_key=t.event_key
                    ) THEN COALESCE(t.manual_commission, 0) + COALESCE((
                      SELECT SUM(COALESCE(cr.commission, e.commission, 0))
                      FROM trade_executions te
                      LEFT JOIN executions e
                        ON e.account=te.account AND e.exec_id=te.exec_id
                      LEFT JOIN commission_reports cr
                        ON cr.account=te.account AND cr.exec_id=te.exec_id
                      WHERE te.trade_event_key=t.event_key
                    ), 0)
                    ELSE COALESCE(t.commission, 0) END AS calculated_net
                FROM trades t
            """)
            self.con.execute("""
                CREATE VIEW v_net_pnl AS
                SELECT
                  (SELECT COALESCE(SUM(pnl_net),0) FROM trades
                   WHERE event_key IS NULL)
                  + (SELECT COALESCE(SUM(pnl_gross),0) FROM trades
                     WHERE event_key IS NOT NULL)
                  - (SELECT COALESCE(SUM(COALESCE(cr.commission,
                                                  e.commission,0)),0)
                     FROM executions e
                     LEFT JOIN commission_reports cr
                       ON cr.account=e.account AND cr.exec_id=e.exec_id
                     WHERE e.transition_key IS NOT NULL)
                  - (SELECT COALESCE(SUM(manual_commission),0) FROM trades
                     WHERE event_key IS NOT NULL)
                  + (SELECT COALESCE(SUM(delta_usd),0) FROM corrections)
                  AS net_usd
            """)

    # ---- identity and provenance --------------------------------------
    def _start_run(self, venue: str) -> int:
        provenance = release_provenance()
        with self.con:
            cur = self.con.execute(
                "INSERT INTO runs (started,run_kind,venue,account,code_commit,"
                "config_sha256,tree_dirty) VALUES (?,?,?,?,?,?,?)",
                (_utcnow(), self.run_kind, venue, None,
                 provenance["code_commit"],
                 provenance["config_sha256"],
                 int(provenance["tree_dirty"])))
        return cur.lastrowid

    def run_provenance(self) -> dict:
        row = self.con.execute(
            "SELECT code_commit,config_sha256,tree_dirty,account,venue "
            "FROM runs WHERE run_id=?", (self.run_id,)).fetchone()
        return dict(code_commit=row[0], config_sha256=row[1],
                    tree_dirty=bool(row[2]), account=row[3], venue=row[4])

    def bind_identity(self, account: str, venue: str | None = None, *,
                      generation: str | None = None):
        """Permanently bind this DB to one venue/account generation."""
        venue = venue or self.venue
        if not account:
            raise RuntimeError("cannot bind durable state without an account")
        row = self.con.execute(
            "SELECT venue, account, generation FROM runtime_identity "
            "WHERE singleton=1").fetchone()
        now = _utcnow()
        if row and (row[0] != venue or row[1] != account or
                    (generation is not None and row[2] != generation)):
            raise RuntimeError(
                "STATE IDENTITY MISMATCH: durable state belongs to "
                f"{row[0]}/{row[1]}, connected to {venue}/{account}; "
                "refusing to trade")
        with self.con:
            if row is None:
                generation = generation or hashlib.sha256(
                    f"{venue}:{account}:{now}".encode()).hexdigest()[:16]
                self.con.execute(
                    "INSERT INTO runtime_identity "
                    "(singleton,venue,account,generation,bound_at) "
                    "VALUES (1,?,?,?,?)", (venue, account, generation, now))
                # Rows created by the pre-FSM journal had no account and were
                # already reflected in its historical trade ledger.  Bind
                # that provenance once and mark it accounted so the first
                # nightly replay does not confuse migrated history with a
                # newly captured, unapplied broker execution.  Rows captured
                # after identity binding always carry the real account and
                # must pass through a normal FSM transition.
                self.con.execute(
                    "UPDATE executions SET account=? "
                    "WHERE account IS NULL OR account='UNBOUND'", (account,))
                self.con.execute(
                    "UPDATE order_events SET account=? "
                    "WHERE account IS NULL OR account='UNBOUND'", (account,))
                self.con.execute(
                    "UPDATE position_snapshots SET account=? "
                    "WHERE account IS NULL OR account='UNBOUND'", (account,))
                legacy_key = f"legacy-bind:{generation}"
                self.con.execute(
                    "INSERT OR IGNORE INTO applied_executions "
                    "(account,exec_id,event_key,applied_at) "
                    "SELECT ?,exec_id,?,? FROM executions "
                    "WHERE account=? AND exec_id IS NOT NULL "
                    "AND transition_key IS NULL "
                    "AND (run_id IS NULL OR run_id<>?)",
                    (account, legacy_key, now, account, self.run_id))
            self.con.execute("UPDATE runs SET account=? WHERE run_id=?",
                             (account, self.run_id))
        self.account = account
        self.venue = venue

    @property
    def generation(self) -> str | None:
        row = self.con.execute(
            "SELECT generation FROM runtime_identity WHERE singleton=1"
        ).fetchone()
        return row[0] if row else None

    def identity(self) -> dict | None:
        row = self.con.execute(
            "SELECT venue,account,generation,bound_at FROM runtime_identity "
            "WHERE singleton=1").fetchone()
        return (dict(venue=row[0], account=row[1], generation=row[2],
                     bound_at=row[3]) if row else None)

    def _acct(self, account: str | None = None) -> str:
        return account or self.account or "UNBOUND"

    # ---- durable order/FSM events -------------------------------------
    def _validate_event_identity(self, event_key: str, row, sym: str, *,
                                 event_type: str | None = None) -> None:
        if row is None:
            return
        account, venue, stored_sym, stored_type = row
        conflicts = []
        if account != self._acct():
            conflicts.append(f"account {account}!={self._acct()}")
        if venue != self.venue:
            conflicts.append(f"venue {venue}!={self.venue}")
        if stored_sym != sym:
            conflicts.append(f"sym {stored_sym}!={sym}")
        if event_type is not None and stored_type != event_type:
            conflicts.append(f"event_type {stored_type}!={event_type}")
        if conflicts:
            raise sqlite3.IntegrityError(
                f"broker event key collision {event_key}: "
                + "; ".join(conflicts))

    def begin_event(self, event_key: str, sym: str, event_type: str, *,
                    status: str = "PENDING", payload: dict | None = None,
                    order_ref=None, order_id=None, perm_id=None):
        if not event_key:
            raise ValueError("broker event key is required")
        now = _utcnow()
        with self.con:
            self.con.execute(
                "INSERT OR IGNORE INTO broker_events "
                "(event_key,run_id,created,updated,account,venue,sym,event_type,"
                "status,order_ref,order_id,perm_id,payload) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (event_key, self.run_id, now, now, self._acct(), self.venue,
                 sym, event_type, status, order_ref, order_id, perm_id,
                 _json(payload or {})))
            row = self.con.execute(
                "SELECT account,venue,sym,event_type,status "
                "FROM broker_events "
                "WHERE event_key=?", (event_key,)).fetchone()
            self._validate_event_identity(
                event_key, row[:4], sym,
                event_type=(event_type if row[4] not in {
                    "COMMITTED", "RESOLVED", "CANCELLED", "FLAT"}
                    else None))

    def update_event(self, event_key: str, status: str, *,
                     payload: dict | None = None, order_ref=None,
                     order_id=None, perm_id=None):
        prior_row = self.con.execute(
            "SELECT status,payload,account,venue,sym,event_type "
            "FROM broker_events WHERE event_key=?",
            (event_key,)).fetchone()
        if prior_row is None:
            raise KeyError(f"unknown broker event {event_key}")
        self._validate_event_identity(
            event_key, prior_row[2:6], prior_row[4])
        terminal = {"COMMITTED", "RESOLVED", "CANCELLED", "FLAT"}
        # Broker callbacks can arrive after the application transaction.
        # Once terminal, an event must never regress to WORKING or
        # FILLED_PENDING (or exchange one terminal claim for another).
        if prior_row[0] in terminal and status != prior_row[0]:
            return False
        sets, vals = ["status=?", "updated=?"], [status, _utcnow()]
        if payload is not None:
            merged = json.loads(prior_row[1])
            merged.update(payload)
            payload_value = _json(merged)
        else:
            payload_value = None
        for name, value in (("payload", payload_value),
                            ("order_ref", order_ref), ("order_id", order_id),
                            ("perm_id", perm_id)):
            if value is not None:
                sets.append(f"{name}=?")
                vals.append(value)
        vals.append(event_key)
        # Keep the terminal-state check in the UPDATE predicate too.  A late
        # callback must not regress an event that another callback/transition
        # made terminal after the SELECT above.
        vals.extend([status])
        with self.con:
            cur = self.con.execute(
                f"UPDATE broker_events SET {', '.join(sets)} WHERE event_key=? "
                "AND (status NOT IN ('COMMITTED','RESOLVED','CANCELLED','FLAT') "
                "OR status=?)",
                vals)
        return cur.rowcount == 1

    def pending_events(self, event_types: Iterable[str] | None = None) -> list[dict]:
        sql = ("SELECT event_key,sym,event_type,status,order_ref,order_id,"
               "perm_id,payload FROM broker_events WHERE account=? "
               "AND venue=? AND status NOT IN ('COMMITTED','RESOLVED',"
               "'CANCELLED','FLAT')")
        args: list = [self._acct(), self.venue]
        if event_types:
            kinds = list(event_types)
            sql += f" AND event_type IN ({','.join('?' for _ in kinds)})"
            args.extend(kinds)
        out = []
        for row in self.con.execute(sql, args):
            out.append(dict(event_key=row[0], sym=row[1], event_type=row[2],
                            status=row[3], order_ref=row[4], order_id=row[5],
                            perm_id=row[6], payload=json.loads(row[7])))
        return out

    def event_status(self, event_key: str) -> str | None:
        row = self.con.execute(
            "SELECT status FROM broker_events WHERE event_key=?",
            (event_key,)).fetchone()
        return row[0] if row else None

    # ---- raw broker capture vs applied executions ---------------------
    def _execution_in_tx(self, sym: str, side: str, qty: float, px: float, *,
                         local_symbol=None, con_id=None, commission=None,
                         exec_id=None, exec_time=None, order_ref=None,
                         order_id=None, perm_id=None, transition_key=None,
                         account=None):
        role = self._side_role(side)
        if role not in {"BUY", "SELL"}:
            raise sqlite3.IntegrityError(
                f"broker execution has invalid side {side!r}")
        for label, value in (("qty", qty), ("px", px)):
            if not math.isfinite(float(value)) or float(value) <= 0:
                raise sqlite3.IntegrityError(
                    f"broker execution {label} must be finite and positive")
        if commission is not None and not math.isfinite(float(commission)):
            raise sqlite3.IntegrityError(
                "broker execution commission must be finite")
        acct = self._acct(account)
        self.con.execute(
            "INSERT OR IGNORE INTO executions "
            "(run_id,ts,account,sym,side,qty,px,local_symbol,con_id,commission,"
            "exec_id,exec_time,order_ref,order_id,perm_id,transition_key) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (self.run_id, _utcnow(), acct, sym, side, qty, px, local_symbol,
             con_id, commission, exec_id, str(exec_time) if exec_time else None,
             order_ref, order_id, perm_id, transition_key))
        if exec_id:
            stored = self.con.execute(
                "SELECT sym,side,qty,px,local_symbol,con_id,order_ref,perm_id "
                "FROM executions WHERE account=? AND exec_id=?",
                (acct, exec_id)).fetchone()
            if stored is None:
                raise sqlite3.IntegrityError(
                    f"execution {acct}/{exec_id} disappeared after insert")

            def side_role(value) -> str | None:
                value = str(value or "").upper()
                if value in ("BUY", "BOT"):
                    return "BUY"
                if value in ("SELL", "SLD"):
                    return "SELL"
                return value or None

            conflicts = []
            if (stored[0] != "UNKNOWN" and sym != "UNKNOWN"
                    and stored[0] != sym):
                conflicts.append(f"sym {stored[0]}!={sym}")
            if (side_role(stored[1]) and side_role(side)
                    and side_role(stored[1]) != side_role(side)):
                conflicts.append(f"side {stored[1]}!={side}")
            for label, old, new in (("qty", stored[2], qty),
                                    ("px", stored[3], px)):
                if abs(float(old) - float(new)) > \
                        1e-9 * max(1.0, abs(float(old)), abs(float(new))):
                    conflicts.append(f"{label} {old}!={new}")
            if (stored[4] and local_symbol and
                    stored[4] != local_symbol):
                conflicts.append(
                    f"local_symbol {stored[4]}!={local_symbol}")
            if (stored[5] is not None and con_id is not None and
                    int(stored[5]) != int(con_id)):
                conflicts.append(f"con_id {stored[5]}!={con_id}")
            if stored[6] and order_ref and stored[6] != order_ref:
                conflicts.append(f"order_ref {stored[6]}!={order_ref}")
            if (stored[7] not in (None, 0) and perm_id not in (None, 0)
                    and int(stored[7]) != int(perm_id)):
                conflicts.append(f"perm_id {stored[7]}!={perm_id}")
            if conflicts:
                raise sqlite3.IntegrityError(
                    f"conflicting replay for execution {acct}/{exec_id}: "
                    + "; ".join(conflicts))
            # An early callback can lack orderRef/contract enrichment while a
            # later broker rescan has it.  The unique execId row remains the
            # same fact, but fill missing provenance instead of leaving it
            # stranded under UNKNOWN and therefore unrecoverable by symbol.
            self.con.execute(
                "UPDATE executions SET "
                "sym=CASE WHEN sym='UNKNOWN' AND ?<>'UNKNOWN' THEN ? ELSE sym END,"
                "side=COALESCE(side,?),"
                "local_symbol=CASE WHEN local_symbol IS NULL OR "
                "local_symbol='' THEN ? ELSE local_symbol END,"
                "con_id=COALESCE(con_id,?),"
                "exec_time=COALESCE(exec_time,?),"
                "order_ref=CASE WHEN order_ref IS NULL OR order_ref='' "
                "THEN ? ELSE order_ref END,"
                "order_id=COALESCE(order_id,?),"
                "perm_id=CASE WHEN perm_id IS NULL OR perm_id=0 "
                "THEN ? ELSE perm_id END "
                "WHERE account=? AND exec_id=?",
                (sym, sym, side, local_symbol, con_id,
                 str(exec_time) if exec_time else None, order_ref,
                 order_id, perm_id, acct, exec_id))
        if exec_id and commission is not None:
            self.con.execute(
                "UPDATE executions SET commission=COALESCE(commission, ?) "
                "WHERE account=? AND exec_id=?", (commission, acct, exec_id))
        if exec_id and transition_key is not None:
            self.con.execute(
                "UPDATE executions SET transition_key=COALESCE(transition_key, ?) "
                "WHERE account=? AND exec_id=?",
                (transition_key, acct, exec_id))

    @staticmethod
    def _side_role(side) -> str | None:
        value = str(side or "").upper()
        if value in ("BUY", "BOT"):
            return "BUY"
        if value in ("SELL", "SLD"):
            return "SELL"
        return value or None

    def _validate_transition_executions(self, event_key: str,
                                        entry_ids: set[str],
                                        exit_ids: set[str],
                                        *, allow_applied_entries: bool,
                                        expected_roles: dict[str, str]
                                        | None = None,
                                        ) -> dict[str, str | None]:
        """Validate exact execution roles and reject cross-event replays.

        Entry IDs may already be applied when a later partial-exit trade maps
        the original entry fee. Exit IDs are the position-changing facts for
        that close and may never have been applied by a different event.
        """
        if expected_roles is not None and (entry_ids or exit_ids):
            raise ValueError(
                "expected_roles cannot be combined with entry/exit IDs")
        if entry_ids & exit_ids:
            raise sqlite3.IntegrityError(
                f"execution IDs used as both entry and exit: "
                f"{sorted(entry_ids & exit_ids)}")
        existing: dict[str, str | None] = {}
        roles = (expected_roles if expected_roles is not None else
                 {**{x: "BUY" for x in entry_ids},
                  **{x: "SELL" for x in exit_ids}})
        for xid, want in roles.items():
            row = self.con.execute(
                "SELECT e.side,a.event_key FROM executions e "
                "LEFT JOIN applied_executions a ON a.account=e.account "
                "AND a.exec_id=e.exec_id WHERE e.account=? AND e.exec_id=?",
                (self._acct(), xid)).fetchone()
            if row is None:
                raise sqlite3.IntegrityError(
                    f"transition {event_key} references missing execution "
                    f"{self._acct()}/{xid}")
            if self._side_role(row[0]) != want:
                raise sqlite3.IntegrityError(
                    f"transition {event_key} uses {xid} as {want}, "
                    f"broker side is {row[0]}")
            existing[xid] = row[1]
            if row[1] and row[1] != event_key:
                if expected_roles is not None or xid in exit_ids or \
                        not allow_applied_entries:
                    raise sqlite3.IntegrityError(
                        f"execution {self._acct()}/{xid} already applied by "
                        f"{row[1]}, refusing replay in {event_key}")
        return existing

    def execution(self, sym: str, side: str, qty: float, px: float, *,
                  local_symbol=None, con_id=None, commission=None,
                  exec_id=None, exec_time=None, order_ref=None, order_id=None,
                  perm_id=None, transition_key=None, account=None):
        if not exec_id:
            raise sqlite3.IntegrityError(
                "raw broker execution requires an execId")
        with self.con:
            self._execution_in_tx(
                sym, side, qty, px, local_symbol=local_symbol, con_id=con_id,
                commission=commission, exec_id=exec_id, exec_time=exec_time,
                order_ref=order_ref, order_id=order_id, perm_id=perm_id,
                transition_key=transition_key, account=account)

    def commission(self, exec_id: str, commission: float, *, account=None,
                   currency=None, realized_pnl=None):
        if not exec_id:
            raise sqlite3.IntegrityError(
                "commission report requires an execId")
        if not math.isfinite(float(commission)):
            raise sqlite3.IntegrityError(
                "commission report must be finite")
        if (realized_pnl is not None and
                not math.isfinite(float(realized_pnl))):
            raise sqlite3.IntegrityError(
                "commission realized P&L must be finite when present")
        acct = self._acct(account)
        with self.con:
            self.con.execute(
                "INSERT INTO commission_reports "
                "(account,exec_id,commission,currency,realized_pnl,received_at) "
                "VALUES (?,?,?,?,?,?) ON CONFLICT(account,exec_id) DO UPDATE "
                "SET commission=excluded.commission,currency=excluded.currency,"
                "realized_pnl=excluded.realized_pnl,"
                "received_at=excluded.received_at",
                (acct, exec_id, commission, currency, realized_pnl, _utcnow()))
            self.con.execute(
                "UPDATE executions SET commission=? WHERE account=? AND exec_id=?",
                (commission, acct, exec_id))
            self._refresh_mapped_trades(acct, exec_id)

    def is_execution_applied(self, exec_id: str, *, account=None) -> bool:
        return self.con.execute(
            "SELECT 1 FROM applied_executions WHERE account=? AND exec_id=?",
            (self._acct(account), exec_id)).fetchone() is not None

    def executions_for_ref(self, order_ref: str, *, account=None) -> list[dict]:
        rows = self.con.execute(
            "SELECT exec_id,side,qty,px,local_symbol,con_id,commission,exec_time "
            "FROM executions WHERE account=? AND order_ref=? ORDER BY id",
            (self._acct(account), order_ref))
        return [dict(exec_id=r[0], side=r[1], qty=float(r[2]), px=float(r[3]),
                     local_symbol=r[4], con_id=r[5], commission=r[6],
                     exec_time=r[7]) for r in rows]

    def unapplied_executions(self, sym: str, *, side: str | None = None,
                             role: str | None = None,
                             account=None) -> list[dict]:
        acct = self._acct(account)
        sql = ("SELECT e.exec_id,e.side,e.qty,e.px,e.local_symbol,e.con_id,"
               "COALESCE(cr.commission,e.commission),e.exec_time,e.order_ref,"
               "e.order_id,e.perm_id FROM executions e "
               "LEFT JOIN applied_executions a ON a.account=e.account "
               "AND a.exec_id=e.exec_id LEFT JOIN commission_reports cr "
               "ON cr.account=e.account AND cr.exec_id=e.exec_id "
               "WHERE e.account=? AND e.sym=? AND a.exec_id IS NULL")
        args: list = [acct, sym]
        if side:
            aliases = ("SELL", "SLD") if side.upper() == "SELL" else ("BUY", "BOT")
            sql += " AND UPPER(e.side) IN (?,?)"
            args.extend(aliases)
        if role:
            sql += " AND e.order_ref LIKE ?"
            args.append(f"trendbot:{sym}:{role}%")
        sql += " ORDER BY e.id"
        return [dict(exec_id=r[0], side=r[1], qty=float(r[2]), px=float(r[3]),
                     local_symbol=r[4], con_id=r[5], commission=r[6],
                     exec_time=r[7], order_ref=r[8], order_id=r[9],
                     perm_id=r[10]) for r in self.con.execute(sql, args)]

    def unapplied_emergency_executions(self, *, account=None) -> list[dict]:
        """Unapplied kill-switch close/cover rows, across synthetic labels."""
        acct = self._acct(account)
        rows = self.con.execute(
            "SELECT e.sym,e.exec_id,e.side,e.qty,e.px,e.local_symbol,e.con_id,"
            "COALESCE(cr.commission,e.commission),e.exec_time,e.order_ref,"
            "e.order_id,e.perm_id FROM executions e "
            "LEFT JOIN applied_executions a ON a.account=e.account "
            "AND a.exec_id=e.exec_id LEFT JOIN commission_reports cr "
            "ON cr.account=e.account AND cr.exec_id=e.exec_id "
            "WHERE e.account=? AND a.exec_id IS NULL "
            "AND (e.order_ref LIKE 'trendbot:UNMANAGED-%:close%' "
            "OR e.order_ref LIKE 'trendbot:COVER-%:close%') ORDER BY e.id",
            (acct,))
        return [dict(sym=r[0], exec_id=r[1], side=r[2], qty=float(r[3]),
                     px=float(r[4]), local_symbol=r[5], con_id=r[6],
                     commission=r[7], exec_time=r[8], order_ref=r[9],
                     order_id=r[10], perm_id=r[11]) for r in rows]

    def applied_order_qty(self, order_key: str, *, account=None) -> float:
        row = self.con.execute(
            "SELECT applied_qty FROM order_progress WHERE account=? "
            "AND order_key=?", (self._acct(account), order_key)).fetchone()
        return float(row[0]) if row else 0.0

    # ---- atomic position transition -----------------------------------
    def commit_transition(self, event_key: str, sym: str, event_type: str,
                          position: dict, *, executions: list[dict] | None = None,
                          trade: dict | None = None,
                          entry_exec_ids: Iterable[str] = (),
                          exit_exec_ids: Iterable[str] = (),
                          order_key: str | None = None,
                          filled_total: float | None = None,
                          payload: dict | None = None) -> bool:
        """Atomically apply one broker event; return False when replayed."""
        executions = executions or []
        entry_ids = {x for x in entry_exec_ids if x}
        exit_ids = {x for x in exit_exec_ids if x}
        if any(not row.get("exec_id") for row in executions):
            raise sqlite3.IntegrityError(
                f"transition {event_key} requires an execId for every "
                "broker execution")
        execution_ids = {row["exec_id"] for row in executions}
        unmapped = execution_ids - entry_ids - exit_ids
        if unmapped:
            raise sqlite3.IntegrityError(
                f"transition {event_key} has executions without an applied "
                f"entry/exit role: {sorted(unmapped)}")
        acct, now = self._acct(), _utcnow()
        with self.con:
            prior = self.con.execute(
                "SELECT status,account,venue,sym,event_type "
                "FROM broker_events WHERE event_key=?",
                (event_key,)).fetchone()
            if prior:
                self._validate_event_identity(
                    event_key, prior[1:5], sym,
                    event_type=(event_type if prior[0] == "COMMITTED"
                                else None))
                if prior[0] == "COMMITTED":
                    return False
            if prior is None:
                self.con.execute(
                    "INSERT INTO broker_events "
                    "(event_key,run_id,created,updated,account,venue,sym,"
                    "event_type,status,payload) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (event_key, self.run_id, now, now, acct, self.venue, sym,
                     event_type, "APPLYING", _json(payload or {})))
            else:
                self.con.execute(
                    "UPDATE broker_events SET status='APPLYING',updated=?,"
                    "event_type=?,payload=? WHERE event_key=?",
                    (now, event_type, _json(payload or {}), event_key))

            for item in executions:
                row = dict(item)
                self._execution_in_tx(
                    sym, row.pop("side"), row.pop("qty"), row.pop("px"),
                    transition_key=event_key, **row)

            applied_before = self._validate_transition_executions(
                event_key, entry_ids, exit_ids,
                allow_applied_entries=trade is not None)

            if trade is not None:
                self._trade_in_tx(event_key=event_key, **trade)
                for xid in entry_ids:
                    self.con.execute(
                        "INSERT OR IGNORE INTO trade_executions "
                        "(trade_event_key,account,exec_id,leg) VALUES (?,?,?,'ENTRY')",
                        (event_key, acct, xid))
                for xid in exit_ids:
                    self.con.execute(
                        "INSERT OR IGNORE INTO trade_executions "
                        "(trade_event_key,account,exec_id,leg) VALUES (?,?,?,'EXIT')",
                        (event_key, acct, xid))
                self._refresh_trade(event_key)

            for xid in entry_ids | exit_ids:
                if not applied_before[xid]:
                    self.con.execute(
                        "INSERT INTO applied_executions "
                        "(account,exec_id,event_key,applied_at) "
                        "VALUES (?,?,?,?)", (acct, xid, event_key, now))
                self.con.execute(
                    "UPDATE executions SET transition_key="
                    "COALESCE(transition_key,?) WHERE account=? AND exec_id=?",
                    (event_key, acct, xid))
            if order_key is not None and filled_total is not None:
                self.con.execute(
                    "INSERT INTO order_progress "
                    "(account,order_key,applied_qty,event_key,updated) "
                    "VALUES (?,?,?,?,?) ON CONFLICT(account,order_key) DO UPDATE "
                    "SET applied_qty=MAX(order_progress.applied_qty,"
                    "excluded.applied_qty),event_key=excluded.event_key,"
                    "updated=excluded.updated",
                    (acct, order_key, filled_total, event_key, now))

            fsm_state = position.get("fsm_state") or (
                "HELD_PROTECTED" if position.get("position") else "FLAT")
            self.con.execute(
                "INSERT INTO position_fsm "
                "(account,venue,sym,fsm_state,qty,con_id,stop_order_id,"
                "last_event_key,version,payload,updated) VALUES (?,?,?,?,?,?,?,?,1,?,?) "
                "ON CONFLICT(account,venue,sym) DO UPDATE SET "
                "fsm_state=excluded.fsm_state,qty=excluded.qty,"
                "con_id=excluded.con_id,stop_order_id=excluded.stop_order_id,"
                "last_event_key=excluded.last_event_key,"
                "version=position_fsm.version+1,payload=excluded.payload,"
                "updated=excluded.updated",
                (acct, self.venue, sym, fsm_state,
                 float(position.get("qty") or 0), position.get("con_id"),
                 position.get("stop_order_id"), event_key, _json(position), now))
            self.con.execute(
                "UPDATE broker_events SET status='COMMITTED',updated=? "
                "WHERE event_key=?", (now, event_key))
        return True

    def commit_execution_event(self, event_key: str, sym: str,
                               event_type: str, *, executions: list[dict],
                               payload: dict | None = None) -> bool:
        """Apply an emergency execution that has no strategy-position FSM.

        Kill-switch covers of erroneous shorts and closes of unmanaged
        positions still need durable, idempotent execution provenance.  They
        deliberately do not invent a strategy entry basis or a position_fsm
        row for an unmanaged symbol.
        """
        acct, now = self._acct(), _utcnow()
        if not executions or any(not row.get("exec_id") for row in executions):
            raise sqlite3.IntegrityError(
                f"execution-only event {event_key} requires an execId for "
                "every broker execution")
        roles: dict[str, str] = {}
        for execution in executions:
            xid = execution.get("exec_id")
            if not xid:
                continue
            role = self._side_role(execution.get("side"))
            if role not in {"BUY", "SELL"}:
                raise sqlite3.IntegrityError(
                    f"execution-only event {event_key} has invalid side "
                    f"{execution.get('side')} for {xid}")
            if xid in roles and roles[xid] != role:
                raise sqlite3.IntegrityError(
                    f"execution-only event {event_key} gives {xid} "
                    f"conflicting sides {roles[xid]} and {role}")
            roles[xid] = role
        ids = set(roles)
        with self.con:
            prior = self.con.execute(
                "SELECT status,account,venue,sym,event_type "
                "FROM broker_events WHERE event_key=?",
                (event_key,)).fetchone()
            if prior:
                self._validate_event_identity(
                    event_key, prior[1:5], sym,
                    event_type=(event_type if prior[0] == "COMMITTED"
                                else None))
                if prior[0] == "COMMITTED":
                    return False
            if prior is None:
                self.con.execute(
                    "INSERT INTO broker_events "
                    "(event_key,run_id,created,updated,account,venue,sym,"
                    "event_type,status,payload) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (event_key, self.run_id, now, now, acct, self.venue, sym,
                     event_type, "APPLYING", _json(payload or {})))
            else:
                self.con.execute(
                    "UPDATE broker_events SET status='APPLYING',updated=?,"
                    "event_type=?,payload=? WHERE event_key=?",
                    (now, event_type, _json(payload or {}), event_key))
            for item in executions:
                row = dict(item)
                self._execution_in_tx(
                    sym, row.pop("side"), row.pop("qty"), row.pop("px"),
                    transition_key=event_key, **row)
            applied_before = self._validate_transition_executions(
                event_key, set(), set(), allow_applied_entries=False,
                expected_roles=roles)
            for xid in ids:
                if not applied_before[xid]:
                    self.con.execute(
                        "INSERT INTO applied_executions "
                        "(account,exec_id,event_key,applied_at) "
                        "VALUES (?,?,?,?)", (acct, xid, event_key, now))
                self.con.execute(
                    "UPDATE executions SET transition_key="
                    "COALESCE(transition_key,?) WHERE account=? AND exec_id=?",
                    (event_key, acct, xid))
            self.con.execute(
                "UPDATE broker_events SET status='COMMITTED',updated=? "
                "WHERE event_key=?", (now, event_key))
        return True

    def bootstrap_positions(self, positions: dict):
        """One-time import for the existing paper ledger, never for live."""
        acct = self._acct()
        n = self.con.execute(
            "SELECT COUNT(*) FROM position_fsm WHERE account=? AND venue=?",
            (acct, self.venue)).fetchone()[0]
        if n:
            return
        with self.con:
            for sym, position in positions.items():
                key = f"bootstrap:{self.generation}:{sym}"
                state = "HELD_PROTECTED" if position.get("position") else "FLAT"
                self.con.execute(
                    "INSERT INTO position_fsm "
                    "(account,venue,sym,fsm_state,qty,con_id,stop_order_id,"
                    "last_event_key,version,payload,updated) "
                    "VALUES (?,?,?,?,?,?,?,?,1,?,?)",
                    (acct, self.venue, sym, state,
                     float(position.get("qty") or 0), position.get("con_id"),
                     position.get("stop_order_id"), key, _json(position),
                     _utcnow()))

    def load_positions(self) -> dict[str, dict]:
        rows = self.con.execute(
            "SELECT sym,payload FROM position_fsm WHERE account=? AND venue=?",
            (self._acct(), self.venue))
        return {sym: json.loads(payload) for sym, payload in rows}

    # ---- accounting ----------------------------------------------------
    def order_event(self, sym: str, kind: str, event: str, *, event_key=None,
                    order_id=None, perm_id=None, qty=None, px=None, note=None):
        with self.con:
            self.con.execute(
                "INSERT OR IGNORE INTO order_events "
                "(run_id,ts,account,sym,kind,event,event_key,order_id,perm_id,"
                "qty,px,note) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (self.run_id, _utcnow(), self._acct(), sym, kind, event,
                 event_key, order_id, perm_id, qty, px, note))

    def _trade_in_tx(self, *, event_key=None, **kw):
        gross = kw.get("pnl_usd")
        comm = kw.get("commission") or 0.0
        manual = kw.get("manual_commission") or 0.0
        verb = "INSERT OR IGNORE" if event_key else "INSERT"
        self.con.execute(
            f"{verb} INTO trades (run_id,event_key,exit_time,book,sym,qty,"
            "entry_time,entry_px,exit_px,reason,pnl_points,pnl_gross,commission,"
            "manual_commission,pnl_net,entry_weight,local_symbol) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (self.run_id, event_key, kw.get("exit_time"), kw.get("book"),
             kw.get("sym"), kw.get("qty"), kw.get("entry_time"),
             kw.get("entry_px"), kw.get("exit_px"), kw.get("reason"),
             kw.get("pnl_points"), gross, comm, manual,
             (gross - comm - manual) if gross is not None else None,
             kw.get("entry_weight"), kw.get("local_symbol")))

    def trade(self, event_key=None, entry_exec_ids=(), exit_exec_ids=(), **kw):
        with self.con:
            self._trade_in_tx(event_key=event_key, **kw)
            if event_key:
                acct = self._acct()
                for leg, ids in (("ENTRY", entry_exec_ids),
                                 ("EXIT", exit_exec_ids)):
                    for xid in ids:
                        if xid:
                            self.con.execute(
                                "INSERT OR IGNORE INTO trade_executions "
                                "(trade_event_key,account,exec_id,leg) "
                                "VALUES (?,?,?,?)", (event_key, acct, xid, leg))
                self._refresh_trade(event_key)

    def _refresh_trade(self, event_key: str):
        row = self.con.execute(
            "SELECT COALESCE(SUM(COALESCE(cr.commission,e.commission,0)),0) "
            "FROM trade_executions te LEFT JOIN executions e "
            "ON e.account=te.account AND e.exec_id=te.exec_id "
            "LEFT JOIN commission_reports cr ON cr.account=te.account "
            "AND cr.exec_id=te.exec_id WHERE te.trade_event_key=?",
            (event_key,)).fetchone()
        fee = float(row[0] or 0)
        self.con.execute(
            "UPDATE trades SET commission=?+COALESCE(manual_commission,0), "
            "pnl_net=pnl_gross-?-COALESCE(manual_commission,0) "
            "WHERE event_key=?", (fee, fee, event_key))

    def _refresh_mapped_trades(self, account: str, exec_id: str):
        keys = [r[0] for r in self.con.execute(
            "SELECT trade_event_key FROM trade_executions "
            "WHERE account=? AND exec_id=?", (account, exec_id))]
        for key in keys:
            self._refresh_trade(key)

    def correction(self, kind: str, ref: str, delta_usd: float, note: str):
        if not kind or not ref or not note:
            raise ValueError("correction kind, ref, and note are required")
        if not math.isfinite(float(delta_usd)):
            raise ValueError("correction delta_usd must be finite")
        if kind in {"emergency_pnl", "basis_pnl"}:
            event = self.con.execute(
                "SELECT event_type,payload FROM broker_events "
                "WHERE event_key=?", (ref,)).fetchone()
            if event is None:
                raise ValueError(
                    f"{kind} correction must reference a broker event")
            if kind == "emergency_pnl" and not str(event[0]).startswith(
                    "EMERGENCY_FLATTEN"):
                raise ValueError(
                    "emergency_pnl must reference an emergency flatten event")
            if kind == "basis_pnl":
                payload = json.loads(event[1] or "{}")
                if (float(payload.get("unbased_qty") or 0) <= 0 and
                        float(payload.get("broker_closed") or 0) <=
                        float(payload.get("booked_qty") or 0)):
                    raise ValueError(
                        "basis_pnl must reference an event with unbased close "
                        "quantity")
        with self.con:
            self.con.execute(
                "INSERT INTO corrections (ts,kind,ref,delta_usd,note) "
                "VALUES (?,?,?,?,?)", (_utcnow(), kind, ref, delta_usd, note))

    def unapplied_execution_count(self, *, account=None) -> int:
        return int(self.con.execute(
            "SELECT COUNT(*) FROM executions e LEFT JOIN applied_executions a "
            "ON a.account=e.account AND a.exec_id=e.exec_id "
            "WHERE e.account=? AND ((e.exec_id IS NOT NULL "
            "AND a.exec_id IS NULL) OR (e.exec_id IS NULL "
            "AND e.transition_key IS NOT NULL))",
            (self._acct(account),)).fetchone()[0])

    def snapshot_positions(self, positions: dict):
        open_pos = {k: v for k, v in positions.items() if v.get("position")}
        with self.con:
            self.con.execute(
                "INSERT INTO position_snapshots (ts,account,snapshot) "
                "VALUES (?,?,?)", (_utcnow(), self._acct(), _json(open_pos)))

    def set_readiness(self, component: str, status: str, detail: str = ""):
        with self.con:
            self.con.execute(
                "INSERT INTO readiness(component,status,detail,updated) "
                "VALUES (?,?,?,?) ON CONFLICT(component) DO UPDATE SET "
                "status=excluded.status,detail=excluded.detail,"
                "updated=excluded.updated",
                (component, status, detail, _utcnow()))

    def readiness(self) -> dict[str, dict]:
        return {r[0]: {"status": r[1], "detail": r[2], "updated": r[3]}
                for r in self.con.execute(
                    "SELECT component,status,detail,updated FROM readiness")}

    def latest_runtime_provenance(self) -> dict | None:
        row = self.con.execute(
            "SELECT run_id,code_commit,config_sha256,tree_dirty,account,venue,"
            "started FROM runs WHERE run_kind='runtime' "
            "ORDER BY run_id DESC LIMIT 1").fetchone()
        return (dict(run_id=row[0], code_commit=row[1],
                     config_sha256=row[2], tree_dirty=bool(row[3]),
                     account=row[4], venue=row[5], started=row[6])
                if row else None)

    def soak_blockers(self) -> list[str]:
        """Durable qualification blockers; broker truth is checked by CLI."""
        blockers = []
        if self.identity() is None:
            blockers.append("runtime identity is unbound")
        if self.con.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            blockers.append("SQLite integrity_check failed")
        held = self.con.execute(
            "SELECT sym,qty FROM position_fsm WHERE account=? AND venue=? "
            "AND ABS(qty)>1e-9", (self._acct(), self.venue)).fetchall()
        if held:
            blockers.append(f"FSM positions are not flat: {held}")
        pending = self.con.execute(
            "SELECT event_key,status FROM broker_events WHERE status NOT IN "
            "('COMMITTED','RESOLVED','CANCELLED','FLAT')").fetchall()
        if pending:
            blockers.append(f"broker events unresolved: {pending[:10]}")
        if self.unapplied_execution_count():
            blockers.append("broker executions remain unapplied")
        unidentified = self.con.execute(
            "SELECT COUNT(*) FROM executions WHERE transition_key IS NOT NULL "
            "AND exec_id IS NULL").fetchone()[0]
        if unidentified:
            blockers.append(
                f"{unidentified} transitioned executions lack execId")
        missing_commission = self.con.execute(
            "SELECT COUNT(*) FROM executions e LEFT JOIN commission_reports c "
            "ON c.account=e.account AND c.exec_id=e.exec_id "
            "WHERE e.transition_key IS NOT NULL AND e.exec_id IS NOT NULL "
            "AND c.exec_id IS NULL AND e.commission IS NULL").fetchone()[0]
        if missing_commission:
            blockers.append(
                f"{missing_commission} applied executions lack commission")
        emergency_gap = self.con.execute(
            "SELECT COUNT(*) FROM broker_events b WHERE "
            "b.event_type LIKE 'EMERGENCY_FLATTEN%' AND NOT EXISTS ("
            "SELECT 1 FROM corrections c WHERE c.ref=b.event_key "
            "AND c.kind='emergency_pnl')").fetchone()[0]
        if emergency_gap:
            blockers.append(
                f"{emergency_gap} emergency events lack P&L correction")
        return blockers

    def start_soak(self, note: str, *, started: str | None = None) -> dict:
        """Append an explicit, release-bound qualification epoch."""
        if not note or not note.strip():
            raise ValueError("soak note is required")
        blockers = self.soak_blockers()
        if blockers:
            raise RuntimeError("soak start refused: " + "; ".join(blockers))
        identity = self.identity()
        provenance = self.run_provenance()
        if (provenance["tree_dirty"] or not provenance["code_commit"] or
                not provenance["config_sha256"]):
            raise RuntimeError("soak start requires clean release provenance")
        started = started or _utcnow()
        with self.con:
            cur = self.con.execute(
                "INSERT INTO soak_epochs "
                "(started,account,venue,generation,code_commit,config_sha256,"
                "note) VALUES (?,?,?,?,?,?,?)",
                (started, identity["account"], identity["venue"],
                 identity["generation"], provenance["code_commit"],
                 provenance["config_sha256"], note.strip()))
        return dict(epoch_id=cur.lastrowid, started=started,
                    account=identity["account"], venue=identity["venue"],
                    generation=identity["generation"],
                    code_commit=provenance["code_commit"],
                    config_sha256=provenance["config_sha256"],
                    note=note.strip())

    def current_soak(self) -> dict | None:
        row = self.con.execute(
            "SELECT s.epoch_id,s.started,s.account,s.venue,s.generation,"
            "s.code_commit,s.config_sha256,s.note,i.invalidated,i.reason "
            "FROM soak_epochs s LEFT JOIN soak_invalidations i "
            "ON i.epoch_id=s.epoch_id ORDER BY s.epoch_id DESC "
            "LIMIT 1").fetchone()
        return (dict(epoch_id=row[0], started=row[1], account=row[2],
                     venue=row[3], generation=row[4], code_commit=row[5],
                     config_sha256=row[6], note=row[7],
                     invalidated_at=row[8], invalidation_reason=row[9])
                if row else None)

    def invalidate_current_soak(self, reason: str) -> bool:
        """Append a permanent invalidation for the current qualification."""
        if not reason or not reason.strip():
            raise ValueError("soak invalidation reason is required")
        current = self.current_soak()
        if current is None:
            return False
        with self.con:
            cur = self.con.execute(
                "INSERT OR IGNORE INTO soak_invalidations "
                "(epoch_id,invalidated,reason) VALUES (?,?,?)",
                (current["epoch_id"], _utcnow(), reason.strip()))
        return cur.rowcount == 1

    def net_pnl(self) -> float:
        return float(self.con.execute(
            "SELECT net_usd FROM v_net_pnl").fetchone()[0])

    def close(self):
        self.con.close()
