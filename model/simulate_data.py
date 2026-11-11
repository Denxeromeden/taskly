"""Append synthetic task rows for normal refreshes or a drift demonstration.

    Drift mode targets high=0.60, medium=0.25 and low=0.15 labels.
    """

import argparse
import math
import os
import random
from collections import Counter
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError

LABELS = ("high", "medium", "low")
DEFAULT_DATABASE_URL = "postgresql+psycopg2://tasks:tasks@localhost:5432/tasks"

NORMAL_SUBJECTS = (
    "login page",
    "checkout flow",
    "API rate limiter",
    "email service",
    "dashboard chart",
    "search endpoint",
    "user profile",
    "billing webhook",
    "cache layer",
    "PDF export",
    "notification worker",
    "auth middleware",
    "database migration",
    "CSV importer",
    "admin panel",
)

NORMAL_EXAMPLES = {
    "high": {
        "titles": (
            "URGENT: {subject} is down in production",
            "Critical bug in {subject}, customers are blocked",
            "Security vulnerability found in {subject}, fix ASAP",
            "{subject} outage - needs immediate attention",
            "Production incident: {subject} throwing 500s for all users",
            "Data loss risk in {subject}, must fix today",
        ),
        "descriptions": (
            "This is blocking all customers and needs to be fixed immediately.",
            "On-call was paged, this cannot wait until next sprint.",
            "Escalated by support, affects every account on the platform.",
            "Revenue-impacting, drop everything else.",
        ),
    },
    "medium": {
        "titles": (
            "Improve {subject} performance",
            "Add validation to {subject}",
            "Refactor {subject} before next release",
            "{subject} needs better error messages",
            "Update {subject} to handle an edge case",
            "Investigate flaky test in {subject}",
        ),
        "descriptions": (
            "Should be done this sprint, not blocking anyone yet.",
            "Would be good to land before the next release.",
            "No customers affected yet but worth prioritizing soon.",
            "Came up in code review, moderate impact.",
        ),
    },
    "low": {
        "titles": (
            "Rename variable in {subject}",
            "Minor typo in {subject} docs",
            "Nice-to-have: dark mode for {subject}",
            "Clean up unused imports in {subject}",
            "Consider refactoring {subject} someday",
            "Cosmetic tweak to {subject} spacing",
        ),
        "descriptions": (
            "No rush, whenever someone has spare time.",
            "Purely cosmetic, doesn't affect functionality.",
            "Someday/maybe, not tied to any deadline.",
            "Low priority cleanup, no user impact.",
        ),
    },
}

DRIFT_EXAMPLES = {
    "high": {
        "subjects": (
            "multi-region identity federation",
            "payment event replay pipeline",
            "regional failover controller",
            "customer data replication",
            "checkout token exchange",
            "warehouse ingestion backlog",
        ),
        "titles": (
            "SEV-1: {subject} is cascading across regions",
            "Critical {subject} failure is blocking live transactions",
            "Immediate security response required for {subject}",
            "Production data integrity incident in {subject}",
            "Revenue-critical outage: {subject} is unavailable",
        ),
        "descriptions": (
            "The incident is affecting multiple regions and active customer sessions.",
            "The recovery queue is growing and new transactions cannot complete.",
            "Immediate containment is required to prevent further data loss.",
            "The primary failover path is exhausted and the service remains degraded.",
        ),
    },
    "medium": {
        "subjects": (
            "schema backfill coordinator",
            "dead-letter queue processor",
            "search index compaction",
            "feature flag evaluator",
            "webhook retry scheduler",
            "audit event export",
        ),
        "titles": (
            "Tune {subject} before the next rollout",
            "Add replay protection to {subject}",
            "Investigate intermittent lag in {subject}",
            "Improve observability for {subject}",
            "Handle an edge case in {subject}",
        ),
        "descriptions": (
            "The issue is limited to a subset of requests but should be addressed soon.",
            "This is not an outage, although processing is slower than expected.",
            "The next release should include a guard for this condition.",
            "A small number of delayed events need investigation.",
        ),
    },
    "low": {
        "subjects": (
            "developer portal theme",
            "report download filename",
            "internal runbook index",
            "support dashboard spacing",
            "archived event labels",
            "admin console icons",
        ),
        "titles": (
            "Polish the wording in {subject}",
            "Align the visual spacing in {subject}",
            "Remove stale examples from {subject}",
            "Consider a cosmetic update to {subject}",
            "Document a minor cleanup for {subject}",
        ),
        "descriptions": (
            "This is a housekeeping task with no impact on customer workflows.",
            "The change can wait until there is spare capacity.",
            "No deadline is attached and the current behavior is acceptable.",
            "This is a small presentation-only improvement.",
        ),
    },
}

DRIFT_LABEL_WEIGHTS = {"high": 0.60, "medium": 0.25, "low": 0.15}


class DataInsertError(RuntimeError):
    """A database failure reported without exposing connection details."""


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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--rows", type=positive_bounded_rows, default=150)
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--mode", choices=("normal", "drift"), default="normal")
    parser.add_argument("--label-noise", type=valid_label_noise, default=0.0)
    return parser


def _allocate_label_counts(rows: int, mode: str, rng: random.Random) -> dict[str, int]:
    if mode == "normal":
        base, remainder = divmod(rows, len(LABELS))
        counts = {label: base for label in LABELS}
        for label in rng.sample(LABELS, remainder):
            counts[label] += 1
        return counts

    if mode == "drift":
        exact = {label: rows * DRIFT_LABEL_WEIGHTS[label] for label in LABELS}
        counts = {label: math.floor(exact[label]) for label in LABELS}
        remaining = rows - sum(counts.values())
        by_remainder = sorted(
            LABELS,
            key=lambda label: (-(exact[label] - counts[label]), LABELS.index(label)),
        )
        for label in by_remainder[:remaining]:
            counts[label] += 1
        return counts

    raise ValueError("mode must be 'normal' or 'drift'")


def generate_records(
    rows: int,
    seed: int | None = None,
    mode: str = "normal",
    label_noise: float = 0.0,
) -> tuple[list[dict[str, Any]], int]:
    if not 1 <= rows <= 5_000:
        raise ValueError("rows must be between 1 and 5000")
    if mode not in ("normal", "drift"):
        raise ValueError("mode must be 'normal' or 'drift'")
    if not math.isfinite(label_noise) or not 0.0 <= label_noise <= 0.5:
        raise ValueError("label_noise must be between 0.0 and 0.5")

    rng = random.Random(seed)
    counts = _allocate_label_counts(rows, mode, rng)
    priorities = [label for label in LABELS for _ in range(counts[label])]
    rng.shuffle(priorities)

    records = []
    for priority in priorities:
        if mode == "normal":
            examples = NORMAL_EXAMPLES[priority]
            subject = rng.choice(NORMAL_SUBJECTS)
        else:
            examples = DRIFT_EXAMPLES[priority]
            subject = rng.choice(examples["subjects"])

        records.append(
            {
                "title": rng.choice(examples["titles"]).format(subject=subject),
                "description": rng.choice(examples["descriptions"]),
                "done": False,
                "priority": priority,
            }
        )

    noisy_rows = int(rows * label_noise)
    for index in rng.sample(range(rows), noisy_rows):
        current_priority = records[index]["priority"]
        alternatives = [label for label in LABELS if label != current_priority]
        records[index]["priority"] = rng.choice(alternatives)

    return records, noisy_rows


def insert_records(records: list[dict[str, Any]], database_url: str) -> None:
    engine = None
    try:
        engine = create_engine(database_url)
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO tasks (title, description, done, priority) "
                    "VALUES (:title, :description, :done, :priority)"
                ),
                records,
            )
    except SQLAlchemyError as exc:
        raise DataInsertError(
            f"database insert failed ({type(exc).__name__}); check the database and tasks table"
        ) from None
    finally:
        if engine is not None:
            engine.dispose()


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    records, noisy_rows = generate_records(
        rows=args.rows,
        seed=args.seed,
        mode=args.mode,
        label_noise=args.label_noise,
    )

    database_url = os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)
    try:
        insert_records(records, database_url)
    except DataInsertError as exc:
        parser.exit(status=1, message=f"error: {exc}\n")

    counts = Counter(record["priority"] for record in records)
    label_counts = ",".join(f"{label}:{counts[label]}" for label in LABELS)
    seed_value = args.seed if args.seed is not None else "random"
    print(
        f"inserted {len(records)} rows into tasks "
        f"(mode={args.mode}, rows={args.rows}, seed={seed_value}, "
        f"label_noise={args.label_noise:g}, noisy_rows={noisy_rows}, "
        f"label_counts={label_counts})"
    )


if __name__ == "__main__":
    main()
