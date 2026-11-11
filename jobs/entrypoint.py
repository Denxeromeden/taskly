"""Prepare database configuration and invoke the ML data and training scripts."""

from __future__ import annotations

import argparse
import math
import os
import subprocess
import sys
from pathlib import Path
from typing import Mapping

from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL
from sqlalchemy.exc import SQLAlchemyError


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


def _child_environment(environment: Mapping[str, str]) -> dict[str, str]:
    child_environment = dict(environment)
    child_environment["DATABASE_URL"] = build_database_url(environment)
    for key in SECRET_ENVIRONMENT:
        child_environment.pop(key, None)
    return child_environment


def _repository_root() -> Path:
    return Path(__file__).resolve().parents[1]


def run_training(environment: Mapping[str, str] | None = None) -> None:
    source_environment = os.environ if environment is None else environment
    tracking_uri = source_environment.get("MLFLOW_TRACKING_URI", "").strip()
    if not tracking_uri:
        raise ValueError("MLFLOW_TRACKING_URI is required")

    repository_root = _repository_root()
    training_script = repository_root / "model" / "train.py"
    subprocess.run(
        [sys.executable, str(training_script)],
        check=True,
        cwd=repository_root,
        env=_child_environment(source_environment),
    )


def count_existing_labeled_rows(database_url: str) -> int:
    """Return the number of labeled task rows, treating a missing table as empty."""
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            table_name = connection.execute(
                text("SELECT to_regclass('public.tasks')")
            ).scalar_one_or_none()
            if table_name is None:
                return 0
            count = connection.execute(
                text("SELECT COUNT(*) FROM public.tasks WHERE priority IS NOT NULL")
            ).scalar_one()
            return int(count)
    except SQLAlchemyError as exc:
        raise RuntimeError(
            f"seed pre-check failed ({type(exc).__name__}); verify database connectivity"
        ) from None
    finally:
        engine.dispose()


def run_seed(environment: Mapping[str, str] | None = None) -> None:
    """Seed the starter data only when the tasks table has no labeled rows."""
    source_environment = os.environ if environment is None else environment
    database_url = build_database_url(source_environment)
    existing_rows = count_existing_labeled_rows(database_url)
    if existing_rows:
        print(f"seed skipped: tasks already contains {existing_rows} labeled rows")
        return

    repository_root = _repository_root()
    seed_script = repository_root / "model" / "generate_data.py"
    result = subprocess.run(
        [sys.executable, str(seed_script)],
        check=False,
        cwd=repository_root,
        env=_child_environment(source_environment),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    if result.returncode != 0:
        # generate_data.py prints its connection URL on success. Keep its output
        # suppressed and report a safe error rather than risking credential logs.
        raise RuntimeError(
            f"seed generator failed with exit code {result.returncode}; "
            "verify database connectivity and the tasks schema"
        )
    print("seed inserted the starter dataset into tasks")


def positive_bounded_rows(value: str) -> int:
    try:
        rows = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an integer") from exc
    if not 1 <= rows <= 5_000:
        raise argparse.ArgumentTypeError("must be between 1 and 5000")
    return rows


def valid_label_noise(value: str) -> float:
    try:
        fraction = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a number between 0.0 and 0.5") from exc
    if not math.isfinite(fraction) or not 0.0 <= fraction <= 0.5:
        raise argparse.ArgumentTypeError("must be a number between 0.0 and 0.5")
    return fraction


def run_add_data(
    rows: int,
    mode: str,
    label_noise: float,
    seed: int | None = None,
    environment: Mapping[str, str] | None = None,
) -> None:
    source_environment = os.environ if environment is None else environment
    repository_root = _repository_root()
    simulator_script = repository_root / "model" / "simulate_data.py"
    command = [
        sys.executable,
        str(simulator_script),
        "--rows",
        str(rows),
        "--mode",
        mode,
        "--label-noise",
        str(label_noise),
    ]
    if seed is not None:
        command.extend(["--seed", str(seed)])
    subprocess.run(
        command,
        check=True,
        cwd=repository_root,
        env=_child_environment(source_environment),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="operation", required=True)
    subparsers.add_parser("seed", help="insert starter rows when no labeled rows exist")
    subparsers.add_parser("train", help="run model/train.py")

    add_data_parser = subparsers.add_parser(
        "add-data", help="append normal or drifted synthetic rows"
    )
    add_data_parser.add_argument(
        "--rows",
        type=positive_bounded_rows,
        default=os.environ.get("ADD_DATA_ROWS", "150"),
    )
    add_data_parser.add_argument(
        "--mode",
        choices=("normal", "drift"),
        default=os.environ.get("ADD_DATA_MODE", "normal"),
    )
    add_data_parser.add_argument(
        "--label-noise",
        type=valid_label_noise,
        default=os.environ.get("ADD_DATA_LABEL_NOISE", "0.0"),
    )
    seed_value = os.environ.get("ADD_DATA_SEED", "").strip()
    add_data_parser.add_argument(
        "--seed", type=int, default=seed_value if seed_value else None
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.operation == "seed":
        run_seed()
    elif args.operation == "train":
        run_training()
    elif args.operation == "add-data":
        run_add_data(
            rows=args.rows,
            mode=args.mode,
            label_noise=args.label_noise,
            seed=args.seed,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
