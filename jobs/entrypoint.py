#!/usr/bin/env python3
"""Runtime wrapper for the Taskly ML Jobs.

Builds the database connection from the mounted credentials and dispatches the
requested subcommand:

    python jobs/entrypoint.py seed
"""

import argparse
import os
import pathlib
import subprocess
import sys
import tempfile

from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL
from sqlalchemy.exc import SQLAlchemyError

DEFAULT_SECRET_DIR = "/var/run/tasks-db"
SECRET_FIELDS = ("engine", "host", "port", "dbname", "username", "password")
APP_DIR = pathlib.Path(__file__).resolve().parent.parent
GENERATOR = APP_DIR / "model" / "generate_data.py"


class ConfigError(Exception):
    """Raised when the runtime configuration is missing or unusable."""


def _strip_trailing_newline(value):
    if value.endswith("\r\n"):
        return value[:-2]
    if value.endswith("\n"):
        return value[:-1]
    return value


def read_secret(directory):
    values = {}
    for field in SECRET_FIELDS:
        path = directory / field
        try:
            raw = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            raise ConfigError(f"missing secret file: {path}") from None
        except OSError as exc:
            raise ConfigError(f"cannot read secret file {path}: {exc.strerror}") from None
        value = _strip_trailing_newline(raw)
        if not value:
            raise ConfigError(f"empty secret file: {path}")
        values[field] = value
    return values


def _pgpass_escape(value):
    return value.replace("\\", "\\\\").replace(":", "\\:")


def write_pgpass(values):
    line = ":".join(
        _pgpass_escape(values[field])
        for field in ("host", "port", "dbname", "username", "password")
    )
    try:
        handle, path = tempfile.mkstemp(prefix=".pgpass-", dir=tempfile.gettempdir())
    except OSError as exc:
        raise ConfigError(f"cannot create PGPASSFILE: {exc.strerror}") from None
    try:
        os.fchmod(handle, 0o600)
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(line + "\n")
    except BaseException:
        os.unlink(path)
        raise
    return path


def build_database_url(values):
    try:
        port = int(values["port"])
    except ValueError:
        raise ConfigError(f"secret file 'port' is not an integer: {values['port']}") from None
    return URL.create(
        drivername="postgresql+psycopg2",
        username=values["username"],
        host=values["host"],
        port=port,
        database=values["dbname"],
        query={"sslmode": "require"},
    )


def check_seed_state(database_url):
    """Return 'missing', 'unlabeled' or 'labeled' for public.tasks."""
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            if connection.execute(text("SELECT to_regclass('public.tasks')")).scalar() is None:
                return "missing"
            labeled = connection.execute(
                text("SELECT count(*) FROM public.tasks WHERE priority IS NOT NULL")
            ).scalar_one()
    finally:
        engine.dispose()
    return "labeled" if labeled else "unlabeled"


def run_generator(env):
    return subprocess.run(
        [sys.executable, str(GENERATOR)], env=env, check=False
    ).returncode


def seed():
    secret_dir = pathlib.Path(os.environ.get("TASKS_DB_SECRET_DIR") or DEFAULT_SECRET_DIR)
    try:
        values = read_secret(secret_dir)
        if not values["engine"].lower().startswith("postgres"):
            raise ConfigError(f"unsupported database engine: {values['engine']}")
        database_url = build_database_url(values).render_as_string(hide_password=True)
        pgpass_path = write_pgpass(values)
    except ConfigError as exc:
        print(f"seed: {exc}", file=sys.stderr, flush=True)
        return 1

    # libpq reads PGPASSFILE from the environment, so the guard query and the
    # generator subprocess must both see it.
    os.environ.pop("PGPASSWORD", None)
    os.environ["PGPASSFILE"] = pgpass_path
    os.environ["DATABASE_URL"] = database_url
    env = os.environ.copy()

    try:
        state = check_seed_state(database_url)
        if state == "labeled":
            print("seed skipped: labeled rows already exist", flush=True)
            return 0
        reason = "does not exist" if state == "missing" else "has no labeled rows"
        print(f"seed: public.tasks {reason}, running {GENERATOR.name}", flush=True)
        return run_generator(env)
    except SQLAlchemyError as exc:
        print(f"seed: cannot read public.tasks: {exc}", file=sys.stderr, flush=True)
        return 1
    finally:
        try:
            os.unlink(pgpass_path)
        except OSError:
            pass


def build_parser():
    parser = argparse.ArgumentParser(prog="entrypoint.py", description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("seed", help="create the initial labeled dataset")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    handlers = {"seed": seed}
    return handlers[args.command]()


if __name__ == "__main__":
    sys.exit(main())
