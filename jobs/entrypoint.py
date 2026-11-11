"""Prepare runtime configuration and invoke the existing model training script."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path
from typing import Mapping

from sqlalchemy.engine import URL


SECRET_ENVIRONMENT = {
    "TASKS_DB_HOST": "host",
    "TASKS_DB_PORT": "port",
    "TASKS_DB_NAME": "dbname",
    "TASKS_DB_USERNAME": "username",
    "TASKS_DB_PASSWORD": "password",
}


def build_database_url(environment: Mapping[str, str]) -> str:
    """Build a PostgreSQL URL while safely encoding credential characters."""
    missing = [key for key in SECRET_ENVIRONMENT if not environment.get(key)]
    if missing:
        raise ValueError(
            "Missing required database environment variables: " + ", ".join(missing)
        )

    try:
        port = int(environment["TASKS_DB_PORT"])
    except ValueError as exc:
        raise ValueError("TASKS_DB_PORT must be an integer") from exc

    if not 1 <= port <= 65535:
        raise ValueError("TASKS_DB_PORT must be between 1 and 65535")

    url = URL.create(
        drivername="postgresql+psycopg2",
        username=environment["TASKS_DB_USERNAME"],
        password=environment["TASKS_DB_PASSWORD"],
        host=environment["TASKS_DB_HOST"],
        port=port,
        database=environment["TASKS_DB_NAME"],
        query={"sslmode": "require"},
    )
    return url.render_as_string(hide_password=False)


def run_training(environment: Mapping[str, str] | None = None) -> None:
    source_environment = os.environ if environment is None else environment
    tracking_uri = source_environment.get("MLFLOW_TRACKING_URI", "").strip()
    if not tracking_uri:
        raise ValueError("MLFLOW_TRACKING_URI is required")

    child_environment = dict(source_environment)
    child_environment["DATABASE_URL"] = build_database_url(source_environment)
    for key in SECRET_ENVIRONMENT:
        child_environment.pop(key, None)

    repository_root = Path(__file__).resolve().parents[1]
    training_script = repository_root / "model" / "train.py"
    subprocess.run(
        [sys.executable, str(training_script)],
        check=True,
        cwd=repository_root,
        env=child_environment,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="operation", required=True)
    subparsers.add_parser("train", help="run model/train.py")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.operation == "train":
        run_training()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
