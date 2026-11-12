from jobs import drift
from jobs.drift import (
    FEATURE_COLUMNS,
    METRIC_NAMES,
    RUN_STATUS_SKIPPED_INSUFFICIENT_DATA,
    build_feature_rows,
    metric_payload,
    parse_existing_metrics,
    select_windows,
)


def _rows(count):
    return [
        {
            "id": index + 1,
            "title": f"Title {index}",
            "description": "A short description",
            "priority": "medium",
        }
        for index in range(count)
    ]


def test_windows_use_latest_200_and_cap_reference_at_2000():
    windows = select_windows(_rows(2500))

    assert len(windows.current) == 200
    assert len(windows.reference) == 2000
    assert windows.reference[0]["id"] == 301
    assert windows.reference[-1]["id"] == 2300
    assert windows.current[0]["id"] == 2301
    assert windows.current[-1]["id"] == 2500


def test_feature_rows_are_aggregate_only():
    rows = _rows(2)
    features = build_feature_rows(rows, ["low", "high"])

    assert tuple(features[0]) == FEATURE_COLUMNS
    assert features[0]["title_characters"] == len("Title 0")
    assert features[0]["title_words"] == 2
    assert features[0]["description_words"] == 3
    assert features[0]["predicted_priority"] == "low"
    assert "title" not in features[0]
    assert "description" not in features[0]
    assert "id" not in features[0]


def test_feature_builder_rejects_mismatched_prediction_count():
    try:
        build_feature_rows(_rows(2), ["low"])
    except ValueError as exc:
        assert "prediction count" in str(exc)
    else:
        raise AssertionError("expected prediction-count validation")


def test_pushgateway_payload_has_stable_metric_set_and_no_dynamic_labels():
    payload = metric_payload({name: 1.0 for name in METRIC_NAMES}).decode()

    assert all(f"# TYPE {name} gauge" in payload for name in METRIC_NAMES)
    assert "pod=" not in payload
    assert "run_uuid=" not in payload
    assert "task_id=" not in payload


def test_previous_pushgateway_metrics_are_parsed_and_preserved():
    parsed = parse_existing_metrics(
        "# TYPE taskly_drift_last_success_timestamp_seconds gauge\n"
        'taskly_drift_last_success_timestamp_seconds{job="taskly-drift-detection"} 123.5\n'
        'taskly_drift_detected{job="taskly-drift-detection"} 1\n'
        'taskly_drift_detected{job="another-job"} 99\n'
    )

    assert parsed["taskly_drift_last_success_timestamp_seconds"] == 123.5
    assert parsed["taskly_drift_detected"] == 1.0
    assert RUN_STATUS_SKIPPED_INSUFFICIENT_DATA == 1


def test_previous_metrics_use_pushgateway_scrape_endpoint(monkeypatch):
    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return (
                b'# TYPE taskly_drift_last_success_timestamp_seconds gauge\n'
                b'taskly_drift_last_success_timestamp_seconds{job="taskly-drift-detection"} 123.5\n'
            )

    request_details = {}

    def fake_urlopen(request, timeout):
        request_details["url"] = request.full_url
        request_details["method"] = request.get_method()
        request_details["timeout"] = timeout
        return Response()

    monkeypatch.setattr(drift, "urlopen", fake_urlopen)
    parsed = drift._read_previous_metrics("http://monitoring-pushgateway:9091/")

    assert request_details == {
        "url": "http://monitoring-pushgateway:9091/metrics",
        "method": "GET",
        "timeout": 5,
    }
    assert parsed["taskly_drift_last_success_timestamp_seconds"] == 123.5


def test_windows_are_empty_when_not_enough_data():
    windows = select_windows(_rows(49))
    assert len(windows.current) == 49
    assert len(windows.reference) == 0
