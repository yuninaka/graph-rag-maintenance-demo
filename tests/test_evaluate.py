from src.evaluate import keyword_coverage

FULL_COVERAGE = 1.0
HALF_COVERAGE = 0.5
NO_COVERAGE = 0.0


def test_keyword_coverage_all_keywords_present() -> None:
    answer = "ベアリングの異音が発生し、注油で対応した。"
    assert keyword_coverage(answer, ["異音", "注油"]) == FULL_COVERAGE


def test_keyword_coverage_partial_match() -> None:
    answer = "ベアリングの異音が発生した。"
    assert keyword_coverage(answer, ["異音", "注油"]) == HALF_COVERAGE


def test_keyword_coverage_no_match() -> None:
    answer = "問題は見つかりませんでした。"
    assert keyword_coverage(answer, ["異音", "注油"]) == NO_COVERAGE


def test_keyword_coverage_empty_keywords_returns_zero() -> None:
    assert keyword_coverage("何らかの回答", []) == NO_COVERAGE
