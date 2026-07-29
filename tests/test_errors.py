from renov_market_scan.collect.errors import STATUS_FOR_ERROR, ErrorAction, classify_tool_error


def test_transient_errors_are_retried():
    assert classify_tool_error("too_many_requests") is ErrorAction.RETRY
    assert classify_tool_error("unavailable") is ErrorAction.RETRY


def test_max_uses_accepts_the_partial_result():
    assert classify_tool_error("max_uses_exceeded") is ErrorAction.ACCEPT_PARTIAL


def test_query_too_long_shortens_the_query():
    assert classify_tool_error("query_too_long") is ErrorAction.SHORTEN_QUERY


def test_request_too_large_reduces_the_domain_list():
    assert classify_tool_error("request_too_large") is ErrorAction.REDUCE_DOMAINS


def test_invalid_input_fails_without_retry():
    assert classify_tool_error("invalid_tool_input") is ErrorAction.FAIL


def test_an_unknown_error_code_fails_rather_than_looping():
    assert classify_tool_error("something_new_from_the_api") is ErrorAction.FAIL


def test_every_documented_code_maps_to_a_status_string():
    for code in (
        "too_many_requests",
        "unavailable",
        "max_uses_exceeded",
        "query_too_long",
        "request_too_large",
        "invalid_tool_input",
    ):
        assert STATUS_FOR_ERROR[code]
