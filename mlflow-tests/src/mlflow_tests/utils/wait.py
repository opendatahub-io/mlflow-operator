"""Decorators for polling conditions and retrying transient failures in MLflow tests."""

from __future__ import annotations

import functools
import logging
import math
import time
from collections.abc import Callable, Mapping
from typing import ParamSpec, TypeVar

P = ParamSpec("P")
T = TypeVar("T")
RetryRule = Callable[[Exception], bool] | None
RetryRules = Mapping[type[Exception], RetryRule]


class WaitTimeoutError(TimeoutError):
    """Raised when :func:`wait_until` reaches its deadline without a successful result."""


def _validate_retry_rules(retry_rules: RetryRules | None) -> dict[type[Exception], RetryRule]:
    """Validate and make a decorator-local copy of retry rules."""
    if retry_rules is None:
        return {}
    if not isinstance(retry_rules, Mapping):
        raise TypeError("retry_rules must be a mapping of exception types to predicates")

    rules = dict(retry_rules)
    for exception_type, predicate in rules.items():
        if not isinstance(exception_type, type) or not issubclass(exception_type, Exception):
            raise TypeError("retry_rules keys must be Exception subclasses")
        if predicate is not None and not callable(predicate):
            raise TypeError("retry_rules values must be predicates or None")
    return rules


def _should_retry(error: Exception, retry_rules: RetryRules) -> bool:
    """Return whether an exception matches a retry rule."""
    for exception_type, predicate in retry_rules.items():
        if isinstance(error, exception_type):
            return predicate is None or predicate(error)
    return False


def wait_until(
    *,
    description: str,
    timeout: float,
    interval: float = 1.0,
    until: Callable[[T], bool] = bool,
    retry_rules: RetryRules | None = None,
    progress_interval: float | None = None,
) -> Callable[[Callable[P, T]], Callable[P, T]]:
    """Decorate a function to poll until its result satisfies a condition.

    The decorated function is called immediately. Later calls are made at
    ``interval`` second intervals only while the deadline has not been reached.
    Returned values are passed to ``until``; a truthy result returns the value
    to the caller. Selected transient exceptions can also be retried. All
    other exceptions are raised immediately.

    Args:
        description: Operation name included in retry logs and timeout errors.
        timeout: Finite, non-negative maximum total wait time in seconds.
        interval: Finite, non-negative delay between attempts in seconds.
        until: Predicate used to decide whether a returned value is ready.
            Defaults to :class:`bool`.
        retry_rules: Mapping of retryable exception types to predicates. A
            value of ``None`` retries every instance of that type; a predicate
            retries only when it returns true. The first matching type wins, so
            list subclass types before their base classes.
        progress_interval: Minimum seconds between INFO logs for pending results.
            If omitted, each pending result is logged as a warning.

    Returns:
        A decorator that preserves the decorated function's arguments and
        returns its successful value.

    Raises:
        ValueError: If a timing value is negative or non-finite.
        TypeError: If ``retry_rules`` is malformed.
        WaitTimeoutError: If no returned value satisfies ``until`` before the
            deadline.
    """
    if not math.isfinite(timeout) or timeout < 0:
        raise ValueError("timeout must be finite and non-negative")
    if not math.isfinite(interval) or interval < 0:
        raise ValueError("interval must be finite and non-negative")
    if progress_interval is not None and (
        not math.isfinite(progress_interval) or progress_interval < 0
    ):
        raise ValueError("progress_interval must be finite and non-negative")
    retry_rules = _validate_retry_rules(retry_rules)

    def decorate(func: Callable[P, T]) -> Callable[P, T]:
        logger = logging.getLogger(func.__module__)

        @functools.wraps(func)
        def wrapped(*args: P.args, **kwargs: P.kwargs) -> T:
            last_progress = time.monotonic()
            deadline = last_progress + timeout
            attempt = 0
            last_value: T | None = None
            last_error: Exception | None = None

            while True:
                if attempt and time.monotonic() >= deadline:
                    raise WaitTimeoutError(
                        f"Timed out waiting for {description} after {timeout}s; "
                        f"last value={last_value!r}, last error={last_error!r}"
                    ) from last_error

                attempt += 1
                retry_error: Exception | None = None
                try:
                    value = func(*args, **kwargs)
                except Exception as error:
                    if not _should_retry(error, retry_rules):
                        raise
                    last_error = error
                    retry_error = error
                else:
                    last_value = value
                    if until(value):
                        return value

                now = time.monotonic()
                remaining = deadline - now
                if remaining <= 0:
                    raise WaitTimeoutError(
                        f"Timed out waiting for {description} after {timeout}s; "
                        f"last value={last_value!r}, last error={last_error!r}"
                    ) from last_error

                if retry_error is not None:
                    logger.warning(
                        "%s failed with %s: %s (attempt %s); retrying in %ss",
                        description,
                        type(retry_error).__name__,
                        retry_error,
                        attempt,
                        min(interval, remaining),
                    )
                elif progress_interval is None:
                    logger.warning(
                        "%s condition not met: %r (attempt %s); retrying in %ss",
                        description,
                        last_value,
                        attempt,
                        min(interval, remaining),
                    )
                elif now - last_progress >= progress_interval:
                    logger.info("Still waiting for %s", description)
                    last_progress = now
                time.sleep(min(interval, remaining))

        return wrapped

    return decorate


def retry(
    *,
    description: str,
    max_attempts: int,
    interval: float = 1.0,
    backoff: Callable[[int], float] | None = None,
    retry_rules: RetryRules | None = None,
) -> Callable[[Callable[P, T]], Callable[P, T]]:
    """Decorate a function to retry selected exceptions a fixed number of times.

    This is the count-based counterpart to :func:`wait_until`: it retries only
    exceptions that match ``retry_rules``. A successful call returns
    immediately; an unlisted exception, a rejected predicate, or the final
    failed attempt raises the original exception.

    Args:
        description: Operation name included in retry logs.
        max_attempts: Total number of calls, including the first attempt.
        interval: Finite, non-negative fixed delay between attempts in seconds
            when ``backoff`` is not supplied.
        backoff: Optional callable receiving the failed attempt number and
            returning a finite, non-negative delay before the next attempt.
        retry_rules: Mapping of retryable exception types to predicates. A
            value of ``None`` retries every instance of that type; a predicate
            retries only when it returns true. The first matching type wins, so
            list subclass types before their base classes.

    Returns:
        A decorator that preserves the decorated function's arguments and
        returns its value after a successful attempt.

    Raises:
        ValueError: If ``max_attempts`` is below one, a delay is negative or
            non-finite, or ``backoff`` returns an invalid delay.
        TypeError: If ``retry_rules`` is malformed.
    """
    if max_attempts < 1:
        raise ValueError("max_attempts must be at least 1")
    if not math.isfinite(interval) or interval < 0:
        raise ValueError("interval must be finite and non-negative")
    retry_rules = _validate_retry_rules(retry_rules)

    def decorate(func: Callable[P, T]) -> Callable[P, T]:
        logger = logging.getLogger(func.__module__)

        @functools.wraps(func)
        def wrapped(*args: P.args, **kwargs: P.kwargs) -> T:
            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except Exception as error:
                    if (
                        not _should_retry(error, retry_rules)
                        or attempt == max_attempts
                    ):
                        raise

                    delay = backoff(attempt) if backoff is not None else interval
                    if not math.isfinite(delay) or delay < 0:
                        raise ValueError("backoff must return a finite, non-negative delay")
                    logger.warning(
                        "%s failed with %s: %s (attempt %s/%s); now retrying in %ss",
                        description,
                        type(error).__name__,
                        error,
                        attempt,
                        max_attempts,
                        delay,
                    )
                    time.sleep(delay)

            raise AssertionError("retry exhausted without returning or raising")

        return wrapped

    return decorate
