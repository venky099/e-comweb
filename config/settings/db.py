"""Database resolution for local development.

PostgreSQL is the target database. But a fresh clone has no Postgres server,
and the DATABASE_URL shipped in ``.env.example`` is a placeholder pointing at
a host called literally ``host`` -- so a first run fails with

    could not translate host name "host" to address

which tells a newcomer nothing useful. This module probes the configured
server and quietly drops to SQLite when nothing is listening, so the project
runs immediately after cloning.

Two boundaries matter:

* **Development only.** ``config/settings/prod.py`` never imports this. A
  production deploy silently running on a local file would be far worse than
  failing to boot.
* **Unreachable, not misconfigured.** The probe only opens a TCP connection.
  If the server answers but the credentials or database name are wrong,
  Django raises as it should -- falling back there would hide a real bug.
"""
import socket
from urllib.parse import urlparse

#: How long to wait for the database port. Long enough for a container still
#: starting, short enough not to be felt on every management command.
PROBE_TIMEOUT_SECONDS = 0.75

DEFAULT_PORTS = {
    "postgres": 5432,
    "postgresql": 5432,
    "postgis": 5432,
    "mysql": 3306,
}


def is_sqlite(url):
    return bool(url) and url.startswith("sqlite")


def host_and_port(url):
    """Pull ``(host, port)`` out of a database URL, or ``None`` if not TCP."""
    if not url or is_sqlite(url):
        return None
    try:
        parsed = urlparse(url)
    except ValueError:
        return None

    host = parsed.hostname
    if not host:
        return None

    # A path-style host is a unix socket, which we cannot probe this way.
    if host.startswith("/"):
        return None

    port = parsed.port or DEFAULT_PORTS.get(parsed.scheme.split("+")[0], 5432)
    return host, port


def server_is_reachable(url, timeout=PROBE_TIMEOUT_SECONDS):
    """True when something accepts TCP connections at the URL's host/port.

    Deliberately shallow: it proves a server exists, not that it will let us
    in. Authentication failures must still surface as errors.
    """
    target = host_and_port(url)
    if target is None:
        return False

    host, port = target
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (OSError, socket.timeout, socket.gaierror, ValueError):
        # Covers refused connections, DNS failures ("host" doesn't resolve),
        # unroutable addresses and timeouts alike.
        return False


def sqlite_config(base_dir, name="db.sqlite3"):
    return {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": base_dir / name,
        "OPTIONS": {
            "transaction_mode": "IMMEDIATE",
            "init_command": "PRAGMA foreign_keys=ON;",
        },
    }


def resolve(env, base_dir, database_url=None):
    """Pick the development database.

    Returns ``(config, backend, reason)`` where ``backend`` is "sqlite" or
    "postgres" and ``reason`` explains the choice for logging.

    Order of precedence:

    1. ``USE_SQLITE=True``  -- forced SQLite, no probe.
    2. ``DB_FALLBACK=False`` -- forced Postgres; an unreachable server is an
       error, which is what you want when a local Postgres is expected.
    3. Otherwise probe, and fall back to SQLite if nothing answers.
    """
    if env.bool("USE_SQLITE", default=False):
        return sqlite_config(base_dir), "sqlite", "USE_SQLITE=True"

    url = database_url or env(
        "DATABASE_URL", default="postgres://postgres:postgres@localhost:5432/ecommerce"
    )

    if is_sqlite(url):
        return sqlite_config(base_dir), "sqlite", "DATABASE_URL is sqlite"

    postgres = env.db_url_config(url)

    if not env.bool("DB_FALLBACK", default=True):
        return postgres, "postgres", "DB_FALLBACK=False"

    if server_is_reachable(url):
        target = host_and_port(url)
        return postgres, "postgres", f"reachable at {target[0]}:{target[1]}"

    target = host_and_port(url)
    where = f"{target[0]}:{target[1]}" if target else url
    return sqlite_config(base_dir), "sqlite", f"no server answering at {where}"
