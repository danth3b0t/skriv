import pytest

from hark_client.context.aliases import extract_terms, spoken_forms
from hark_client.context.types import TermKind


@pytest.mark.parametrize(
    ("canonical", "kind", "expected"),
    [
        ("useSessionStore", TermKind.IDENTIFIER, "use session store"),
        ("parse_args", TermKind.IDENTIFIER, "parse args"),
        ("parse_args", TermKind.IDENTIFIER, "parse underscore args"),
        ("dry-run", TermKind.IDENTIFIER, "dry dash run"),
        ("--dry-run", TermKind.FLAG, "dash dash dry run"),
        ("-v", TermKind.FLAG, "dash v"),
        ("session-store.ts", TermKind.PATH, "session store dot ts"),
        ("src/store.ts", TermKind.PATH, "src slash store dot ts"),
        ("config.json", TermKind.PATH, "config dot jason"),
        ("HTTPServer", TermKind.IDENTIFIER, "http server"),
        ("HTTPServer", TermKind.IDENTIFIER, "h t t p server"),
        ("utf8", TermKind.IDENTIFIER, "utf eight"),
        ("MAX_TERMS", TermKind.IDENTIFIER, "max terms"),
    ],
)
def test_spoken_forms_include_natural_reading(canonical: str, kind: TermKind, expected: str):
    assert expected in spoken_forms(canonical, kind)


def test_constants_are_not_spelled_letter_by_letter():
    assert "m a x terms" not in spoken_forms("MAX_TERMS", TermKind.IDENTIFIER)


def test_flags_always_keep_their_dashes():
    assert all(
        a.startswith(("dash", "double dash")) for a in spoken_forms("--dry-run", TermKind.FLAG)
    )


def test_extract_terms_from_agent_reply():
    reply = (
        "I renamed `useSessionStore` in src/lib/store.ts, added parse_args and a "
        "well-known `session-store` cache; run it with --dry-run."
    )
    terms = dict(extract_terms(reply, prose=True))
    assert terms == {
        "useSessionStore": TermKind.IDENTIFIER,
        "session-store": TermKind.IDENTIFIER,
        "src/lib/store.ts": TermKind.PATH,
        "--dry-run": TermKind.FLAG,
        "parse_args": TermKind.IDENTIFIER,
    }


def test_extract_terms_ignores_plain_words():
    assert extract_terms("The Store is OK and Hello there.", prose=True) == []
