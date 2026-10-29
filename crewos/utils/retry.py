"""Retry only transient inference failures; never credential or input errors."""

import random
from openai import APIConnectionError, APIStatusError


def is_retryable(error):
    return isinstance(error, APIConnectionError) or (
        isinstance(error, APIStatusError)
        and (error.status_code == 429 or 500 <= error.status_code < 600)
    )


def retry_delay(retries, error):
    response = getattr(error, "response", None)
    value = response.headers.get("retry-after") if response is not None else None
    if value is not None:
        try:
            delay = float(value)
            if 0 <= delay <= 60:
                return delay
            return None  # Do not retry earlier than the provider permits.
        except ValueError:
            return None
    return random.uniform(0, min(30, 2**retries))
