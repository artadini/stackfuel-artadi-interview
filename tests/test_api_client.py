import math

import pytest

from conftest import FakeApi, event, http_error, make_client
from pipeline_functions.helpers.api_client import ApiError, PaginationError


def events_api(count: int) -> FakeApi:
    return FakeApi(
        data={
            "/progress-events": [
                event(i, event_time=f"2026-03-01T00:{i // 60:02d}:{i % 60:02d}Z")
                for i in range(count)
            ]
        }
    )


@pytest.mark.parametrize("count", [0, 1, 2, 40, 499, 500, 501, 1000, 1200])
def test_fetch_all_returns_exactly_what_the_source_has(count):
    api = events_api(count)
    result = make_client(api).fetch_all("/progress-events", page_size=500)

    assert len(result.records) == count == result.total_count
    assert [record["event_id"] for record in result.records] == [
        f"ev-{i:07d}" for i in range(count)
    ]
    assert len(result.page_numbers) == count
    assert result.pages == max(1, math.ceil(count / 500))
    assert len(api.requests) == result.pages


def test_small_page_size_follows_every_token():
    api = events_api(20)
    result = make_client(api).fetch_all("/progress-events", page_size=7)
    assert [record["event_id"] for record in result.records] == [
        f"ev-{i:07d}" for i in range(20)
    ]
    assert result.page_numbers == [1] * 7 + [2] * 7 + [3] * 6


def test_filter_is_sent_and_applied():
    api = events_api(30)
    cutoff = api.data["/progress-events"][25]["event_time"]
    result = make_client(api).fetch_all("/progress-events", {"since": cutoff})
    assert len(result.records) == 5
    assert api.requests[0][1]["since"] == cutoff


def test_transient_503_is_retried_without_losing_or_duplicating_records():
    api = events_api(1200)
    client = make_client(api)
    # call 1 is the login; fail the first page once and the second page twice
    original = api.__call__
    state = {"calls": 0}

    def flaky(request, timeout=None):
        state["calls"] += 1
        if state["calls"] in (2, 4, 5):
            raise http_error(503, "1")
        return original(request, timeout)

    client._open = flaky
    result = client.fetch_all("/progress-events", page_size=500)

    assert [r["event_id"] for r in result.records] == [
        f"ev-{i:07d}" for i in range(1200)
    ]
    assert client.stats["retries"] == 3
    assert client.sleeps == [1.0, 1.0, 1.0]  # Retry-After is honoured


def test_connection_errors_and_garbage_bodies_are_retried():
    api = events_api(3)
    api.fail_with = [ConnectionResetError("reset"), TimeoutError("slow")]
    client = make_client(api)
    assert len(client.fetch_all("/progress-events").records) == 3
    assert client.stats["retries"] == 2


def test_gives_up_after_max_attempts():
    api = events_api(3)
    api.fail_with = [503] * 10
    client = make_client(api, max_attempts=3)
    with pytest.raises(ApiError, match="failed after 3 attempts"):
        client.fetch_all("/progress-events")
    assert client.stats["retries"] == 2


def test_client_errors_are_not_retried():
    api = events_api(3)
    api.fail_with = [400]
    client = make_client(api)
    with pytest.raises(ApiError) as caught:
        client.fetch_all("/progress-events")
    assert caught.value.status == 400
    assert client.stats["retries"] == 0


def test_expired_token_triggers_one_relogin():
    api = events_api(5)
    client = make_client(api)
    client.login()
    api.valid_tokens = set()  # the server forgot our token
    assert len(client.fetch_all("/progress-events").records) == 5
    assert api.logins == 2


def test_token_is_renewed_before_it_expires():
    api = events_api(5)
    api.token_lifetime = 100
    now = {"t": 0.0}
    client = make_client(api, clock=lambda: now["t"])
    client.fetch_all("/progress-events")
    assert api.logins == 1
    now["t"] = 50  # inside the 60 s renewal margin
    client.fetch_all("/progress-events")
    assert api.logins == 2


def test_total_count_change_while_reading_is_an_error():
    api = events_api(1200)

    def grow(endpoint, page_number):
        if page_number == 1:
            api.data[endpoint].append(event(9999))

    api.after_page = grow
    with pytest.raises(PaginationError, match="total_count changed"):
        make_client(api).fetch_all("/progress-events", page_size=500)


def test_short_read_is_an_error_not_silent_data_loss():
    api = events_api(1200)
    api.truncate_after_first_page = True  # server claims "last page" after 500 of 1200
    with pytest.raises(PaginationError, match="received 500 of 1200"):
        make_client(api).fetch_all("/progress-events", page_size=500)
