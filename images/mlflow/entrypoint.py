#!/usr/bin/env python3
"""Start the MLflow tracking server using credentials mounted as files."""

import os
import pathlib
import sys
import tempfile

from sqlalchemy.engine import URL

DEFAULT_SECRET_DIR = "/var/run/mlflow-db"
DEFAULT_REGION = "eu-central-1"
DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = "5000"
DEFAULT_ARTIFACTS_PREFIX = "mlflow"
SECRET_FIELDS = ("engine", "host", "port", "dbname", "username", "password")


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
    handle, path = tempfile.mkstemp(prefix=".pgpass-", dir=tempfile.gettempdir())
    try:
        os.fchmod(handle, 0o600)
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(line + "\n")
    except BaseException:
        os.unlink(path)
        raise
    return path


def build_backend_uri(values):
    try:
        port = int(values["port"])
    except ValueError:
        raise ConfigError(f"secret file 'port' is not an integer: {values['port']}") from None
    url = URL.create(
        drivername="postgresql+psycopg2",
        username=values["username"],
        host=values["host"],
        port=port,
        database=values["dbname"],
        query={"sslmode": "require"},
    )
    return url.render_as_string(hide_password=True)


def build_artifacts_destination(bucket, prefix):
    prefix = prefix.strip("/")
    if not prefix:
        return f"s3://{bucket}/"
    return f"s3://{bucket}/{prefix}/"


def main():
    secret_dir = pathlib.Path(os.environ.get("MLFLOW_DB_SECRET_DIR") or DEFAULT_SECRET_DIR)
    bucket = (os.environ.get("MLFLOW_ARTIFACTS_BUCKET") or "").strip()
    prefix = os.environ.get("MLFLOW_ARTIFACTS_PREFIX") or DEFAULT_ARTIFACTS_PREFIX
    region = (os.environ.get("AWS_REGION") or "").strip() or DEFAULT_REGION
    host = os.environ.get("MLFLOW_HOST") or DEFAULT_HOST
    port = os.environ.get("MLFLOW_PORT") or DEFAULT_PORT

    try:
        if not bucket:
            raise ConfigError("MLFLOW_ARTIFACTS_BUCKET is required")
        values = read_secret(secret_dir)
        if not values["engine"].lower().startswith("postgres"):
            raise ConfigError(f"unsupported database engine: {values['engine']}")
        backend_uri = build_backend_uri(values)
        artifacts_destination = build_artifacts_destination(bucket, prefix)
        pgpass_path = write_pgpass(values)
    except ConfigError as exc:
        print(f"mlflow entrypoint: {exc}", file=sys.stderr, flush=True)
        return 1

    env = os.environ.copy()
    env.pop("PGPASSWORD", None)
    env["PGPASSFILE"] = pgpass_path
    env["AWS_REGION"] = region
    env["AWS_DEFAULT_REGION"] = region

    print(
        "mlflow entrypoint: starting server "
        f"host={host} port={port} database={values['dbname']} user={values['username']} "
        f"region={region} artifacts={artifacts_destination}",
        file=sys.stderr,
        flush=True,
    )

    os.execvpe(
        "mlflow",
        [
            "mlflow",
            "server",
            "--host",
            host,
            "--port",
            port,
            "--backend-store-uri",
            backend_uri,
            "--artifacts-destination",
            artifacts_destination,
        ],
        env,
    )


if __name__ == "__main__":
    sys.exit(main())
