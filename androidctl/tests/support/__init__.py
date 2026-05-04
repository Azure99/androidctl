from tests.support.daemon_fakes import patch_cli_context
from tests.support.semantic_contract import (
    SOURCE_SCREEN_ABSENT,
    SOURCE_SCREEN_REQUIRED,
    assert_error_result_spine,
    assert_public_result_spine,
    assert_retained_result_spine,
    assert_truth_spine,
    parse_xml,
    retained_result,
    semantic_result,
    semantic_screen,
)

__all__ = [
    "SOURCE_SCREEN_ABSENT",
    "SOURCE_SCREEN_REQUIRED",
    "assert_error_result_spine",
    "assert_public_result_spine",
    "assert_retained_result_spine",
    "assert_truth_spine",
    "parse_xml",
    "patch_cli_context",
    "retained_result",
    "semantic_result",
    "semantic_screen",
]
