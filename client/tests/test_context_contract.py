from skriv_client.context import ContextSnapshot, canonicalize, collect


def test_collect_never_raises_and_returns_snapshot():
    assert isinstance(collect(deadline_s=0.05), ContextSnapshot)


def test_canonicalize_without_terms_is_identity():
    assert canonicalize("hello world", [], []) == ("hello world", [])
