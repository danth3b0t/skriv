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
    assert extract_terms("The store is OK and Hello there. Store it WHERE?", prose=True) == []


# The real dictation that failed: the previous agent reply contained all of these.
FAILED_REPLY = (
    "Compare Qwen3-ASR with Parakeet and the NeMo GPU-PB backend on the RTX 3060. "
    "Run `hark doctor`, reopen Terminal.app and start `uv run hark run`; the Mac M5 Pro "
    "beats university H100s. Like Superwhisper on Hyprland/Omarchy.\n"
    "## Next Steps For Linux\n"
    "Parakeet stays.\n"
    "```python\nresult = Path(None)\n```"
)


@pytest.mark.parametrize(
    ("term", "kind"),
    [
        ("Qwen3-ASR", TermKind.NAME),
        ("Parakeet", TermKind.NAME),
        ("GPU-PB", TermKind.NAME),
        ("RTX 3060", TermKind.NAME),
        ("hark doctor", TermKind.COMMAND),
        ("Terminal.app", TermKind.NAME),
        ("uv run hark run", TermKind.COMMAND),
        ("M5", TermKind.NAME),
        ("H100", TermKind.NAME),
        ("Superwhisper", TermKind.NAME),
        ("Hyprland", TermKind.NAME),
        ("Omarchy", TermKind.NAME),
        ("NeMo", TermKind.IDENTIFIER),
    ],
)
def test_names_and_commands_are_extracted_from_prose(term: str, kind: TermKind):
    assert dict(extract_terms(FAILED_REPLY, prose=True)).get(term) == kind


@pytest.mark.parametrize(
    "noise",
    ["Compare", "Run", "Like", "Next", "Steps", "Linux", "Path", "None", "H100s", "hark"],
)
def test_sentence_starts_headings_and_code_are_not_names(noise: str):
    assert noise not in dict(extract_terms(FAILED_REPLY, prose=True))


def test_capitalized_word_only_at_sentence_starts_needs_two_and_no_lower_case_use():
    assert extract_terms("Parakeet is fast. Parakeet is local.", prose=True) == [
        ("Parakeet", TermKind.NAME)
    ]
    assert extract_terms("Ghostty is fast. Ghostty or ghostty.", prose=True) == []
    assert extract_terms("Ghostty is fast.", prose=True) == []


def test_names_are_not_taken_from_plain_text_extraction():
    assert dict(extract_terms(FAILED_REPLY)).get("Parakeet") is None


@pytest.mark.parametrize(
    ("canonical", "kind", "expected"),
    [
        ("Qwen3-ASR", TermKind.NAME, {"qwen three asr", "qwen 3 asr", "qwen three a s r"}),
        ("RTX 3060", TermKind.NAME, {"rtx thirty sixty", "rtx three zero six zero", "rtx 3060"}),
        ("Terminal.app", TermKind.NAME, {"terminal dot app"}),
        ("hark doctor", TermKind.COMMAND, {"hark doctor", "harkdoctor"}),
        ("uv run hark run", TermKind.COMMAND, {"uv run hark run"}),
        ("git commit --amend", TermKind.COMMAND, {"git commit dash dash amend"}),
        ("H100", TermKind.NAME, {"h one hundred", "h one oh oh"}),
        ("NeMo", TermKind.IDENTIFIER, {"nemo"}),
        ("x86", TermKind.IDENTIFIER, {"x eighty six"}),
        ("GTX 1080", TermKind.NAME, {"gtx ten eighty"}),
        ("2005", TermKind.NAME, {"twenty oh five"}),
        ("3000", TermKind.NAME, {"three thousand"}),
        ("404", TermKind.NAME, {"four oh four"}),
    ],
)
def test_name_and_command_aliases(canonical: str, kind: TermKind, expected: set[str]):
    assert expected <= set(spoken_forms(canonical, kind))


def test_name_hyphens_are_silent():
    assert not any("dash" in a for a in spoken_forms("Qwen3-ASR", TermKind.NAME))


@pytest.mark.parametrize("canonical", ["setUp", "set_up", "inPlace"])
def test_lower_camel_identifiers_get_no_glued_alias(canonical: str):
    assert canonical.lower().replace("_", "") not in spoken_forms(canonical, TermKind.IDENTIFIER)
