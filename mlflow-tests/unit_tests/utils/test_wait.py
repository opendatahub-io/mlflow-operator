import logging

import pytest

from mlflow_tests.utils.wait import WaitTimeoutError, retry, wait_until


NON_FINITE_VALUES = [float("nan"), float("inf"), float("-inf")]
NON_FINITE_IDS = ["nan", "positive-infinity", "negative-infinity"]


class HttpError(Exception):
    def __init__(self, status_code: int):
        self.status_code = status_code
        super().__init__(f"HTTP {status_code}")


class BaseRetryError(Exception):
    pass


class SpecificRetryError(BaseRetryError):
    pass


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleep_delays: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, delay: float) -> None:
        self.sleep_delays.append(delay)
        self.now += delay


@pytest.fixture
def fake_clock(monkeypatch: pytest.MonkeyPatch) -> FakeClock:
    clock = FakeClock()
    monkeypatch.setattr("mlflow_tests.utils.wait.time.monotonic", clock.monotonic)
    monkeypatch.setattr("mlflow_tests.utils.wait.time.sleep", clock.sleep)
    return clock


@pytest.mark.parametrize(
    "timing_values",
    [
        pytest.param({"timeout": float("nan")}, id="timeout-nan"),
        pytest.param({"timeout": float("inf")}, id="timeout-positive-infinity"),
        pytest.param({"timeout": float("-inf")}, id="timeout-negative-infinity"),
        pytest.param({"timeout": 1, "interval": float("nan")}, id="interval-nan"),
        pytest.param({"timeout": 1, "interval": float("inf")}, id="interval-positive-infinity"),
        pytest.param({"timeout": 1, "interval": float("-inf")}, id="interval-negative-infinity"),
    ],
)
def test_wait_until_rejects_non_finite_timing_values(timing_values: dict[str, float]) -> None:
    with pytest.raises(ValueError, match="finite and non-negative"):
        wait_until(description="application readiness", **timing_values)


@pytest.mark.parametrize(
    "value", NON_FINITE_VALUES, ids=NON_FINITE_IDS
)
def test_retry_rejects_non_finite_interval(value: float) -> None:
    with pytest.raises(ValueError, match="finite and non-negative"):
        retry(description="service connection", max_attempts=2, interval=value)


@pytest.mark.parametrize(
    "value", NON_FINITE_VALUES, ids=NON_FINITE_IDS
)
def test_retry_rejects_non_finite_backoff(value: float) -> None:
    @retry(
        description="service connection",
        max_attempts=2,
        backoff=lambda _attempt: value,
        retry_rules={ConnectionError: None},
    )
    def connect() -> None:
        raise ConnectionError("connection refused")

    with pytest.raises(ValueError, match="finite, non-negative delay"):
        connect()


@pytest.mark.parametrize(
    ("timing_values", "message"),
    [
        pytest.param({"timeout": -1}, "timeout", id="timeout"),
        pytest.param({"timeout": 1, "interval": -1}, "interval", id="interval"),
    ],
)
def test_wait_until_rejects_negative_timing_values(
    timing_values: dict[str, float], message: str
) -> None:
    with pytest.raises(ValueError, match=f"{message} must be finite and non-negative"):
        wait_until(description="application readiness", **timing_values)


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        pytest.param({"max_attempts": 0}, "max_attempts must be at least 1", id="max-attempts"),
        pytest.param(
            {"max_attempts": 1, "interval": -1},
            "interval must be finite and non-negative",
            id="interval",
        ),
    ],
)
def test_retry_rejects_invalid_configuration(
    kwargs: dict[str, int], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        retry(description="service connection", **kwargs)


@pytest.mark.parametrize(
    ("retry_rules", "message"),
    [
        pytest.param({"not-an-exception": None}, "keys", id="invalid-key"),
        pytest.param({ConnectionError: "not-a-predicate"}, "values", id="invalid-value"),
    ],
)
def test_retry_rules_must_map_exception_types_to_predicates_or_none(
    retry_rules: dict[object, object], message: str
) -> None:
    with pytest.raises(TypeError, match=f"retry_rules {message}"):
        retry(description="service connection", max_attempts=1, retry_rules=retry_rules)
    with pytest.raises(TypeError, match=f"retry_rules {message}"):
        wait_until(description="application readiness", timeout=1, retry_rules=retry_rules)


def test_retry_rejects_negative_backoff() -> None:
    @retry(
        description="service connection",
        max_attempts=2,
        backoff=lambda _attempt: -1,
        retry_rules={ConnectionError: None},
    )
    def connect() -> None:
        raise ConnectionError("connection refused")

    with pytest.raises(ValueError, match="finite, non-negative delay"):
        connect()


def test_wait_until_logs_the_value_that_caused_a_retry(
    caplog: pytest.LogCaptureFixture, fake_clock: FakeClock
) -> None:
    values = iter(["Pending", "Running"])

    @wait_until(
        description="application readiness",
        timeout=1,
        interval=0,
        until=lambda status: status == "Running",
    )
    def get_status() -> str:
        return next(values)

    assert get_status() == "Running"
    assert "application readiness condition not met: 'Pending'" in caplog.text


def test_wait_until_throttles_pending_logs_without_throttling_polls(
    caplog: pytest.LogCaptureFixture, fake_clock: FakeClock
) -> None:
    caplog.set_level(logging.INFO)

    @wait_until(
        description="Job to complete",
        timeout=40,
        interval=2,
        retry_rules={ConnectionError: None},
        progress_interval=15,
    )
    def job_complete() -> bool:
        if fake_clock.now == 4:
            raise ConnectionError("connection refused")
        return fake_clock.now == 38

    assert job_complete()
    assert fake_clock.sleep_delays == [2] * 19
    assert [record.levelno for record in caplog.records] == [
        logging.WARNING,
        logging.INFO,
        logging.INFO,
    ]
    assert caplog.messages[1:] == ["Still waiting for Job to complete"] * 2


def test_wait_until_does_not_invoke_again_at_the_deadline(fake_clock: FakeClock) -> None:
    attempts = 0

    @wait_until(description="application readiness", timeout=5, interval=10)
    def get_status() -> bool:
        nonlocal attempts
        attempts += 1
        return False

    with pytest.raises(WaitTimeoutError, match="application readiness"):
        get_status()

    assert attempts == 1
    assert fake_clock.sleep_delays == [5]


def test_wait_until_retries_a_listed_exception_then_succeeds(
    caplog: pytest.LogCaptureFixture, fake_clock: FakeClock
) -> None:
    attempts = 0

    @wait_until(
        description="application readiness",
        timeout=1,
        interval=0,
        retry_rules={ConnectionError: None},
    )
    def get_status() -> bool:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise ConnectionError("connection refused")
        return True

    assert get_status() is True
    assert attempts == 2
    assert "application readiness failed with ConnectionError: connection refused" in caplog.text


def test_wait_until_timeout_reports_the_last_retryable_exception(fake_clock: FakeClock) -> None:
    error = ConnectionError("connection refused")
    attempts = 0

    @wait_until(
        description="application readiness",
        timeout=5,
        interval=10,
        retry_rules={ConnectionError: None},
    )
    def get_status() -> bool:
        nonlocal attempts
        attempts += 1
        raise error

    with pytest.raises(WaitTimeoutError, match="last value=None, last error=ConnectionError") as error_info:
        get_status()

    assert attempts == 1
    assert error_info.value.__cause__ is error
    assert fake_clock.sleep_delays == [5]


def test_wait_until_exits_on_an_unlisted_exception() -> None:
    attempts = 0

    @wait_until(description="application readiness", timeout=1, retry_rules={ConnectionError: None})
    def get_status() -> bool:
        nonlocal attempts
        attempts += 1
        raise ValueError("invalid request")

    with pytest.raises(ValueError, match="invalid request"):
        get_status()
    assert attempts == 1


def test_wait_until_exits_when_retry_predicate_rejects_an_exception() -> None:
    attempts = 0

    @wait_until(
        description="MLflow API request",
        timeout=1,
        retry_rules={HttpError: lambda error: error.status_code >= 500},
    )
    def request() -> bool:
        nonlocal attempts
        attempts += 1
        raise HttpError(400)

    with pytest.raises(HttpError, match="HTTP 400"):
        request()
    assert attempts == 1


def test_wait_until_does_not_retry_predicate_errors() -> None:
    attempts = 0

    def invalid_predicate(_value: bool) -> bool:
        raise AssertionError("unexpected response shape")

    @wait_until(
        description="application readiness",
        timeout=1,
        retry_rules={Exception: None},
        until=invalid_predicate,
    )
    def get_status() -> bool:
        nonlocal attempts
        attempts += 1
        return True

    with pytest.raises(AssertionError, match="unexpected response shape"):
        get_status()
    assert attempts == 1


def test_retry_logs_the_exception_that_caused_a_retry(caplog: pytest.LogCaptureFixture) -> None:
    attempts = 0

    @retry(
        description="service connection",
        max_attempts=3,
        interval=0,
        retry_rules={ConnectionError: None},
    )
    def connect() -> bool:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise ConnectionError("connection refused")
        return True

    assert connect() is True
    assert attempts == 3
    assert "service connection failed with ConnectionError: connection refused" in caplog.text


def test_retry_raises_the_final_retryable_exception() -> None:
    error = ConnectionError("connection refused")
    attempts = 0

    @retry(
        description="service connection",
        max_attempts=2,
        interval=0,
        retry_rules={ConnectionError: None},
    )
    def connect() -> None:
        nonlocal attempts
        attempts += 1
        raise error

    with pytest.raises(ConnectionError) as error_info:
        connect()

    assert attempts == 2
    assert error_info.value is error


def test_retry_passes_failed_attempts_to_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    backoff_attempts = []
    sleep_delays = []
    attempts = 0
    monkeypatch.setattr("mlflow_tests.utils.wait.time.sleep", sleep_delays.append)

    @retry(
        description="service connection",
        max_attempts=3,
        backoff=lambda attempt: backoff_attempts.append(attempt) or attempt,
        retry_rules={ConnectionError: None},
    )
    def connect() -> bool:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise ConnectionError("connection refused")
        return True

    assert connect() is True
    assert backoff_attempts == [1, 2]
    assert sleep_delays == [1, 2]


def test_retry_retries_connection_error_then_exits_on_unlisted_exception(
    caplog: pytest.LogCaptureFixture,
) -> None:
    attempts = 0

    @retry(
        description="service connection",
        max_attempts=3,
        interval=0,
        retry_rules={ConnectionError: None},
    )
    def invalid_request() -> bool:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise ConnectionError("connection refused")
        raise ValueError("invalid request")

    with pytest.raises(ValueError, match="invalid request"):
        invalid_request()
    assert attempts == 2
    assert "service connection failed with ConnectionError: connection refused" in caplog.text


def test_retry_retries_5xx_then_exits_on_4xx(caplog: pytest.LogCaptureFixture) -> None:
    attempts = 0

    @retry(
        description="MLflow API request",
        max_attempts=3,
        interval=0,
        retry_rules={HttpError: lambda error: error.status_code >= 500},
    )
    def request() -> bool:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise HttpError(503)
        raise HttpError(400)

    with pytest.raises(HttpError, match="HTTP 400"):
        request()
    assert attempts == 2
    assert "MLflow API request failed with HttpError: HTTP 503" in caplog.text


def test_retry_applies_the_rule_for_each_exception_type() -> None:
    attempts = 0

    def is_connection_refused(error: Exception) -> bool:
        return isinstance(error, ConnectionError) and str(error) == "connection refused"

    def is_server_error(error: Exception) -> bool:
        return isinstance(error, HttpError) and error.status_code >= 500

    @retry(
        description="MLflow API request",
        max_attempts=4,
        interval=0,
        retry_rules={
            ConnectionError: is_connection_refused,
            HttpError: is_server_error,
        },
    )
    def request() -> bool:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise ConnectionError("connection refused")
        if attempts == 2:
            raise HttpError(503)
        raise HttpError(400)

    with pytest.raises(HttpError, match="HTTP 400"):
        request()
    assert attempts == 3


@pytest.mark.parametrize(
    ("retry_rules", "expected_attempts"),
    [
        pytest.param(
            {SpecificRetryError: None, BaseRetryError: lambda _error: False},
            2,
            id="subclass-before-base",
        ),
        pytest.param(
            {BaseRetryError: lambda _error: False, SpecificRetryError: None},
            1,
            id="base-before-subclass",
        ),
    ],
)
def test_retry_rules_use_the_first_matching_exception_type(
    retry_rules: dict[type[Exception], object], expected_attempts: int
) -> None:
    attempts = 0

    @retry(
        description="service connection",
        max_attempts=2,
        interval=0,
        retry_rules=retry_rules,
    )
    def connect() -> bool:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise SpecificRetryError("specific failure")
        return True

    if expected_attempts == 1:
        with pytest.raises(SpecificRetryError, match="specific failure"):
            connect()
    else:
        assert connect() is True
    assert attempts == expected_attempts


def test_wait_until_times_out_with_a_triageable_description() -> None:
    @wait_until(
        description="archival trace root span",
        timeout=0,
        until=lambda trace: trace["status"] == "Running",
    )
    def get_trace() -> dict[str, str]:
        return {"status": "Pending"}

    with pytest.raises(WaitTimeoutError, match="archival trace root span.*Pending"):
        get_trace()
