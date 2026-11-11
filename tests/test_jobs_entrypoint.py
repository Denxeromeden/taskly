import subprocess
from pathlib import Path

import pytest
from sqlalchemy.engine import make_url

from jobs.entrypoint import (
    build_database_url,
    build_parser,
    run_add_data,
    run_seed,
    run_training,
)


BASE_ENVIRONMENT = {
    "TASKS_DB_HOST": "tasks-db.example.internal",
    "TASKS_DB_PORT": "5432",
    "TASKS_DB_NAME": "tasks",
    "TASKS_DB_USERNAME": "tasks_app",
    "TASKS_DB_PASSWORD": "p@ss:/?#% word",
    "MLFLOW_TRACKING_URI": "http://mlflow.mlflow.svc.cluster.local:5000",
}


def test_database_url_encodes_password_and_requires_ssl():
    database_url = build_database_url(BASE_ENVIRONMENT)
    parsed = make_url(database_url)

    assert parsed.drivername == "postgresql+psycopg2"
    assert parsed.username == "tasks_app"
    assert parsed.password == BASE_ENVIRONMENT["TASKS_DB_PASSWORD"]
    assert parsed.host == "tasks-db.example.internal"
    assert parsed.port == 5432
    assert parsed.database == "tasks"
    assert parsed.query == {"sslmode": "require"}
    assert "p@ss:/?#% word" not in database_url


def test_database_url_rejects_missing_credentials():
    environment = dict(BASE_ENVIRONMENT)
    del environment["TASKS_DB_PASSWORD"]

    with pytest.raises(ValueError, match="TASKS_DB_PASSWORD"):
        build_database_url(environment)


def test_database_url_rejects_invalid_port():
    environment = dict(BASE_ENVIRONMENT, TASKS_DB_PORT="70000")

    with pytest.raises(ValueError, match="TASKS_DB_PORT"):
        build_database_url(environment)


def test_run_training_passes_runtime_url_without_logging_it(monkeypatch):
    calls = []

    def fake_run(command, *, check, cwd, env):
        calls.append((command, check, cwd, env))

    monkeypatch.setattr("jobs.entrypoint.subprocess.run", fake_run)
    run_training(BASE_ENVIRONMENT)

    assert len(calls) == 1
    command, check, cwd, environment = calls[0]
    assert command[-1].endswith("/model/train.py")
    assert check is True
    assert cwd == Path(__file__).resolve().parents[1]
    assert environment["MLFLOW_TRACKING_URI"] == BASE_ENVIRONMENT["MLFLOW_TRACKING_URI"]
    assert "sslmode=require" in environment["DATABASE_URL"]
    assert BASE_ENVIRONMENT["TASKS_DB_PASSWORD"] not in environment["DATABASE_URL"]
    assert "TASKS_DB_PASSWORD" not in environment
    assert "TASKS_DB_USERNAME" not in environment


def test_seed_skips_when_labeled_rows_already_exist(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr("jobs.entrypoint.count_existing_labeled_rows", lambda _url: 25)
    monkeypatch.setattr("jobs.entrypoint.subprocess.run", lambda *args, **kwargs: calls.append(args))

    run_seed(BASE_ENVIRONMENT)

    assert calls == []
    assert "seed skipped" in capsys.readouterr().out


def test_seed_runs_generator_without_exposing_its_stdout(monkeypatch):
    calls = []
    monkeypatch.setattr("jobs.entrypoint.count_existing_labeled_rows", lambda _url: 0)

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, stdout="DATABASE_URL with password", stderr="")

    monkeypatch.setattr("jobs.entrypoint.subprocess.run", fake_run)
    run_seed(BASE_ENVIRONMENT)

    assert len(calls) == 1
    command, kwargs = calls[0]
    assert command[-1].endswith("/model/generate_data.py")
    assert kwargs["stdout"] == subprocess.DEVNULL
    assert kwargs["stderr"] == subprocess.PIPE
    assert kwargs["env"]["DATABASE_URL"]
    assert "TASKS_DB_PASSWORD" not in kwargs["env"]


def test_add_data_passes_validated_options_and_database_url(monkeypatch):
    calls = []

    def fake_run(command, *, check, cwd, env):
        calls.append((command, check, cwd, env))

    monkeypatch.setattr("jobs.entrypoint.subprocess.run", fake_run)
    run_add_data(250, "drift", 0.2, 17, BASE_ENVIRONMENT)

    assert len(calls) == 1
    command, check, cwd, environment = calls[0]
    assert command[-8:] == [
        "--rows", "250", "--mode", "drift", "--label-noise", "0.2", "--seed", "17"
    ]
    assert check is True
    assert cwd == Path(__file__).resolve().parents[1]
    assert "sslmode=require" in environment["DATABASE_URL"]
    assert "TASKS_DB_PASSWORD" not in environment


def test_add_data_parser_uses_workflow_environment_defaults(monkeypatch):
    monkeypatch.setenv("ADD_DATA_ROWS", "321")
    monkeypatch.setenv("ADD_DATA_MODE", "drift")
    monkeypatch.setenv("ADD_DATA_LABEL_NOISE", "0.15")
    monkeypatch.setenv("ADD_DATA_SEED", "23")

    args = build_parser().parse_args(["add-data"])

    assert args.rows == 321
    assert args.mode == "drift"
    assert args.label_noise == 0.15
    assert args.seed == 23


def test_add_data_parser_rejects_out_of_range_values():
    with pytest.raises(SystemExit):
        build_parser().parse_args(["add-data", "--rows", "5001"])
    with pytest.raises(SystemExit):
        build_parser().parse_args(["add-data", "--label-noise", "0.6"])
