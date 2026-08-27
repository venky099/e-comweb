"""Tests for the development database fallback.

The behaviour under test is the reason a fresh clone runs at all, so it is
worth pinning down: which database gets chosen, and -- just as important --
when the fallback must *not* kick in.
"""
from pathlib import Path
from unittest import mock

import environ
from django.test import SimpleTestCase

from config.settings import db

BASE_DIR = Path("/tmp/project")

PLACEHOLDER_URL = "postgres://user:password@host:5432/dbname"
LOCAL_URL = "postgres://postgres:postgres@localhost:5432/ecommerce"


def make_env(**values):
    """An ``environ.Env`` reading only the values given."""
    instance = environ.Env()
    instance.ENVIRON = {k: str(v) for k, v in values.items()}
    return instance


class HostParsingTests(SimpleTestCase):
    def test_extracts_host_and_port(self):
        self.assertEqual(db.host_and_port(PLACEHOLDER_URL), ("host", 5432))

    def test_defaults_the_port_by_scheme(self):
        self.assertEqual(
            db.host_and_port("postgres://user:pw@db.example.com/app"),
            ("db.example.com", 5432),
        )
        self.assertEqual(
            db.host_and_port("mysql://user:pw@db.example.com/app"),
            ("db.example.com", 3306),
        )

    def test_explicit_port_wins(self):
        self.assertEqual(db.host_and_port("postgres://u:p@db:6543/app")[1], 6543)

    def test_sqlite_and_sockets_are_not_probeable(self):
        self.assertIsNone(db.host_and_port("sqlite:///db.sqlite3"))
        self.assertIsNone(db.host_and_port(""))
        self.assertIsNone(db.host_and_port(None))

    def test_is_sqlite(self):
        self.assertTrue(db.is_sqlite("sqlite:///x.db"))
        self.assertFalse(db.is_sqlite(PLACEHOLDER_URL))
        self.assertFalse(db.is_sqlite(""))


class ReachabilityTests(SimpleTestCase):
    def test_unresolvable_host_is_unreachable(self):
        """The exact failure a fresh clone hits: the literal host 'host'."""
        self.assertFalse(db.server_is_reachable(PLACEHOLDER_URL, timeout=0.2))

    def test_reachable_when_something_accepts_the_connection(self):
        with mock.patch.object(db.socket, "create_connection") as connect:
            connect.return_value.__enter__ = lambda self: self
            connect.return_value.__exit__ = lambda self, *a: None
            self.assertTrue(db.server_is_reachable(LOCAL_URL))

    def test_refused_connection_is_unreachable(self):
        with mock.patch.object(db.socket, "create_connection", side_effect=ConnectionRefusedError):
            self.assertFalse(db.server_is_reachable(LOCAL_URL))

    def test_timeout_is_unreachable(self):
        with mock.patch.object(db.socket, "create_connection", side_effect=TimeoutError):
            self.assertFalse(db.server_is_reachable(LOCAL_URL))


class ResolveTests(SimpleTestCase):
    def test_use_sqlite_forces_sqlite_without_probing(self):
        with mock.patch.object(db, "server_is_reachable") as probe:
            config, backend, reason = db.resolve(make_env(USE_SQLITE=True), BASE_DIR)
        probe.assert_not_called()
        self.assertEqual(backend, "sqlite")
        self.assertIn("USE_SQLITE", reason)
        self.assertIn("sqlite3", config["ENGINE"])

    def test_falls_back_when_no_server_answers(self):
        with mock.patch.object(db, "server_is_reachable", return_value=False):
            config, backend, reason = db.resolve(
                make_env(DATABASE_URL=PLACEHOLDER_URL), BASE_DIR
            )
        self.assertEqual(backend, "sqlite")
        self.assertIn("no server answering at host:5432", reason)
        self.assertIn("sqlite3", config["ENGINE"])

    def test_uses_postgres_when_the_server_answers(self):
        with mock.patch.object(db, "server_is_reachable", return_value=True):
            config, backend, reason = db.resolve(
                make_env(DATABASE_URL=LOCAL_URL), BASE_DIR
            )
        self.assertEqual(backend, "postgres")
        self.assertIn("postgresql", config["ENGINE"])
        self.assertIn("reachable", reason)

    def test_db_fallback_false_keeps_postgres_even_when_absent(self):
        """Opting out must mean a missing server is an error, not a fallback."""
        with mock.patch.object(db, "server_is_reachable", return_value=False) as probe:
            config, backend, reason = db.resolve(
                make_env(DATABASE_URL=PLACEHOLDER_URL, DB_FALLBACK=False), BASE_DIR
            )
        probe.assert_not_called()
        self.assertEqual(backend, "postgres")
        self.assertIn("postgresql", config["ENGINE"])
        self.assertIn("DB_FALLBACK", reason)

    def test_sqlite_database_url_is_honoured(self):
        config, backend, _reason = db.resolve(
            make_env(DATABASE_URL="sqlite:///custom.db"), BASE_DIR
        )
        self.assertEqual(backend, "sqlite")

    def test_default_url_is_probed_when_none_configured(self):
        with mock.patch.object(db, "server_is_reachable", return_value=False):
            _config, backend, reason = db.resolve(make_env(), BASE_DIR)
        self.assertEqual(backend, "sqlite")
        self.assertIn("localhost:5432", reason)

    def test_sqlite_config_enforces_foreign_keys(self):
        config = db.sqlite_config(BASE_DIR)
        self.assertIn("foreign_keys=ON", config["OPTIONS"]["init_command"])
