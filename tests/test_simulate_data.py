from collections import Counter

import pytest
from sqlalchemy import create_engine, text

from model.simulate_data import build_parser, generate_records, main


def test_normal_mode_is_balanced_and_reproducible():
    first, first_noisy = generate_records(rows=151, seed=42, mode="normal")
    second, second_noisy = generate_records(rows=151, seed=42, mode="normal")

    assert first == second
    assert first_noisy == second_noisy == 0
    counts = Counter(record["priority"] for record in first)
    assert max(counts.values()) - min(counts.values()) <= 1
    assert all(record["done"] is False for record in first)


def test_drift_mode_uses_documented_distribution_and_shifted_vocabulary():
    records, noisy_rows = generate_records(rows=150, seed=42, mode="drift")

    assert noisy_rows == 0
    assert Counter(record["priority"] for record in records) == {
        "high": 90,
        "medium": 38,
        "low": 22,
    }
    assert all("login page" not in record["title"] for record in records)
    assert all("checkout flow" not in record["title"] for record in records)


def test_label_noise_changes_the_requested_number_of_labels():
    clean, _ = generate_records(rows=100, seed=7, mode="normal")
    noisy, noisy_rows = generate_records(
        rows=100,
        seed=7,
        mode="normal",
        label_noise=0.15,
    )

    changed = sum(
        clean_record["priority"] != noisy_record["priority"]
        for clean_record, noisy_record in zip(clean, noisy)
    )
    assert noisy_rows == changed == 15


def test_insert_only_writes_task_fields_and_reports_no_connection_details(
    tmp_path, monkeypatch, capsys
):
    database_path = tmp_path / "tasks.db"
    database_url = f"sqlite:///{database_path}"
    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE tasks ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "title VARCHAR NOT NULL, "
                "description VARCHAR, "
                "done BOOLEAN NOT NULL DEFAULT 0, "
                "priority VARCHAR)"
            )
        )
    engine.dispose()

    monkeypatch.setenv("DATABASE_URL", database_url)
    main(["--rows", "9", "--seed", "23", "--mode", "normal", "--label-noise", "0.2"])

    engine = create_engine(database_url)
    with engine.connect() as connection:
        rows = connection.execute(
            text("SELECT title, description, done, priority FROM tasks")
        ).mappings().all()
    engine.dispose()

    assert len(rows) == 9
    assert all(row["title"] for row in rows)
    assert all(row["done"] in (0, False) for row in rows)
    assert all(row["priority"] in {"high", "medium", "low"} for row in rows)
    output = capsys.readouterr().out
    assert "inserted 9 rows into tasks" in output
    assert "mode=normal" in output
    assert "seed=23" in output
    assert database_url not in output


@pytest.mark.parametrize(
    "arguments",
    [
        ["--rows", "0"],
        ["--rows", "5001"],
        ["--rows", "1.5"],
        ["--label-noise", "-0.1"],
        ["--label-noise", "0.5001"],
        ["--label-noise", "nan"],
        ["--label-noise", "inf"],
    ],
)
def test_parser_rejects_out_of_range_arguments(arguments):
    with pytest.raises(SystemExit):
        build_parser().parse_args(arguments)
