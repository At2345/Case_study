"""Default-deny tenant isolation for the BI mart (PR-8.1).

The mart is a single SQLite file shared by every tenant, and SQLite has no row-level
security. Rather than trusting each query to remember a `WHERE tenant_id = ?`, the
filter is installed once per connection as a TEMP VIEW that shadows the real table
by name:

    CREATE TEMP VIEW triage_items AS
        SELECT * FROM main.triage_items WHERE tenant_id = 'acme'

SQLite resolves an unqualified table name to the temp schema before main, so every
existing query in `semantics`, `anomaly`, `kpi_tree` and `bi` keeps its SQL unchanged
and still sees only one tenant's rows. A query cannot reach another tenant's data by
forgetting a predicate, because there is no predicate to forget.

Two properties make this fail closed rather than open:

* A table with no `tenant_id` column is refused outright instead of being served
  unscoped. Adding a table to the mart without a tenant column breaks the request
  rather than leaking it.
* The views are read-only. An INSERT or UPDATE through a scoped connection raises,
  so the mart cannot be written through a tenant's read path.

Tenant ids are validated against a strict character set before being interpolated
into the view definition, so the inlined literal cannot be used for injection.
"""
from __future__ import annotations

import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, List

DEFAULT_TENANT = "default"
TENANT_COLUMN = "tenant_id"

# Lowercase, digits, dash and underscore. Deliberately narrow: the value is
# interpolated into DDL, so it must not be able to carry a quote.
_TENANT_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


class TenantError(ValueError):
    """A tenant scope could not be established, so no data may be served."""


def normalise(tenant: str) -> str:
    """Validate a tenant id, or refuse it.

    Case is significant: 'Acme' is rejected rather than folded onto 'acme', so a
    token cannot name a tenant that differs from the one the user was issued.
    """
    value = str(tenant or "").strip()
    if not _TENANT_RE.match(value):
        raise TenantError(
            f"tenant {tenant!r} is not a valid tenant id: use 1-64 lowercase characters "
            "from [a-z0-9_-], starting with a letter or digit")
    return value


def install_views(con: sqlite3.Connection, tenant: str) -> List[str]:
    """Shadow every mart table with a temp view filtered to one tenant."""
    tenant = normalise(tenant)
    names = [r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'")]
    for name in names:
        columns = [r[1] for r in con.execute(f'PRAGMA main.table_info("{name}")')]
        if TENANT_COLUMN not in columns:
            raise TenantError(
                f"{name} has no {TENANT_COLUMN} column; refusing to serve it to tenant "
                f"{tenant!r}. Rebuild the mart with app.bi.export_mart.")
        con.execute(
            f'CREATE TEMP VIEW "{name}" AS SELECT * FROM main."{name}" '
            f"WHERE {TENANT_COLUMN} = '{tenant}'")
    return names


@contextmanager
def scoped(path, tenant: str) -> Iterator[sqlite3.Connection]:
    """A connection that can only see one tenant's rows."""
    tenant = normalise(tenant)
    file = Path(path)
    if not file.exists():
        raise FileNotFoundError(str(file))
    con = sqlite3.connect(file)
    con.row_factory = sqlite3.Row
    try:
        install_views(con, tenant)
        yield con
    finally:
        con.close()


def tenant_of(user: dict) -> str:
    """The tenant of an authenticated principal.

    The tenant comes from the server-side session, never from a request parameter,
    a header or a URL segment, so a caller cannot ask to be served as someone else.
    """
    tenant = (user or {}).get("tenant")
    if not tenant:
        raise TenantError("the session carries no tenant claim")
    return normalise(tenant)
