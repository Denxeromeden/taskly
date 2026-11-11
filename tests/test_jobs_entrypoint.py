from sqlalchemy.engine import make_url

from jobs.entrypoint import build_database_url, run_training


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

    try:
        build_database_url(environment)
    except ValueError as error:
        assert "TASKS_DB_PASSWORD" in str(error)
    else:
        raise AssertionError("Expected missing database password to be rejected")


def test_database_url_rejects_invalid_port():
    environment = dict(BASE_ENVIRONMENT, TASKS_DB_PORT="70000")

    try:
        build_database_url(environment)
    except ValueError as error:
        assert "TASKS_DB_PORT" in str(error)
    else:
        raise AssertionError("Expected invalid database port to be rejected")


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
    assert cwd.name == "taskly-main"
    assert environment["MLFLOW_TRACKING_URI"] == BASE_ENVIRONMENT["MLFLOW_TRACKING_URI"]
    assert "sslmode=require" in environment["DATABASE_URL"]
    assert BASE_ENVIRONMENT["TASKS_DB_PASSWORD"] not in environment["DATABASE_URL"]
    assert "TASKS_DB_PASSWORD" not in environment
    assert "TASKS_DB_USERNAME" not in environment
