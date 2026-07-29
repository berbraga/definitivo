"""Classify web-search tool errors, which arrive inside an HTTP 200 response.

The API returns 200 with a web_search_tool_result_error block rather than
raising, so retry policy cannot be driven by HTTP status alone.
"""

from enum import StrEnum


class ErrorAction(StrEnum):
    """What the adapter should do about a tool error."""

    RETRY = "retry"
    ACCEPT_PARTIAL = "accept_partial"
    SHORTEN_QUERY = "shorten_query"
    REDUCE_DOMAINS = "reduce_domains"
    FAIL = "fail"


ACTION_FOR_ERROR: dict[str, ErrorAction] = {
    "too_many_requests": ErrorAction.RETRY,
    "unavailable": ErrorAction.RETRY,
    "max_uses_exceeded": ErrorAction.ACCEPT_PARTIAL,
    "query_too_long": ErrorAction.SHORTEN_QUERY,
    "request_too_large": ErrorAction.REDUCE_DOMAINS,
    "invalid_tool_input": ErrorAction.FAIL,
}

STATUS_FOR_ERROR: dict[str, str] = {
    "too_many_requests": "limite_de_taxa",
    "unavailable": "indisponivel",
    "max_uses_exceeded": "busca_truncada",
    "query_too_long": "query_longa",
    "request_too_large": "requisicao_grande",
    "invalid_tool_input": "erro_query",
}

STATUS_UNKNOWN_ERROR = "erro_desconhecido"


def classify_tool_error(error_code: str) -> ErrorAction:
    """Map an error code to an action. Unknown codes fail rather than loop."""
    return ACTION_FOR_ERROR.get(error_code, ErrorAction.FAIL)


def status_for_error(error_code: str) -> str:
    """The pt-BR status recorded in the cache for this error code."""
    return STATUS_FOR_ERROR.get(error_code, STATUS_UNKNOWN_ERROR)
