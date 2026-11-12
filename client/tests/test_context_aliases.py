import re

import pytest

from hark_client.context import aliases
from hark_client.context.aliases import extract_terms, spoken_forms
from hark_client.context.types import Term, TermKind, TermSource
from hark_client.context.words import TERMINAL_COMMANDS, merge_terms, with_terminal_commands


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
        ("256", TermKind.NAME, {"two fifty six", "two hundred fifty six"}),
        ("50051", TermKind.NAME, {"fifty thousand fifty one", "five oh oh five one"}),
        # not said as spelled (LEXICON), versions, optional ":", leading prefixes
        ("Hyprland", TermKind.NAME, {"hyper land", "hyperland"}),
        ("hark ctl toggle", TermKind.COMMAND, {"hark control toggle", "hark c t l toggle"}),
        ("SQLite", TermKind.NAME, {"sequel light"}),
        ("read_wav", TermKind.IDENTIFIER, {"read wave"}),
        ("tdt-0.6b-v3", TermKind.NAME, {"tdt zero point six b v three"}),
        (
            "node:22-alpine",
            TermKind.NAME,
            {"node colon twenty two alpine", "node twenty two alpine"},
        ),
        (
            "~/.kube/config",
            TermKind.PATH,
            {"tilde slash dot kube slash config", "kube slash config"},
        ),
        ("@/lib/utils", TermKind.PATH, {"at slash lib slash utils"}),
        ("$XDG_RUNTIME_DIR", TermKind.IDENTIFIER, {"dollar xdg runtime dir"}),
    ],
)
def test_name_and_command_aliases(canonical: str, kind: TermKind, expected: set[str]):
    assert expected <= set(spoken_forms(canonical, kind))


def test_name_hyphens_are_silent():
    assert not any("dash" in a for a in spoken_forms("Qwen3-ASR", TermKind.NAME))


@pytest.mark.parametrize("canonical", ["setUp", "set_up", "inPlace"])
def test_lower_camel_identifiers_get_no_glued_alias(canonical: str):
    assert canonical.lower().replace("_", "") not in spoken_forms(canonical, TermKind.IDENTIFIER)


@pytest.mark.parametrize(
    "path",
    ["report_v256.json", "docs/research/2026-11-12/run_42.log", "src/v2/hark_client/a/b/ctx_10.py"],
)
def test_long_and_numbered_paths_are_said_in_words_first(path: str):
    forms = spoken_forms(path, TermKind.PATH)
    assert re.fullmatch(r"[a-z ]+", forms[0])  # no digits, no punctuation
    assert any(re.search(r"\d", f) for f in forms)  # digits as written are still offered
    assert len(forms) <= aliases.MAX_ALIASES


# A tiny stand-in for /usr/share/dict/words.
WORDS = frozenset(
    [
        "configure",
        "run",
        "library",
        "break",
        "is",
        "fast",
        "rock",
        "beat",
        "team",
        "feature",
        "well",
        "known",
        "good",
        "branch",
        "don",
        "see",
        "ax",
        "where",
        "and",
        "or",
        "this",
        "text",
        "unicode",
        "cafe",
    ]
)


@pytest.fixture
def word_list(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(aliases, "english_words", lambda: WORDS)


@pytest.mark.usefixtures("word_list")
def test_word_list_finds_names_acronyms_and_kebab_terms_anywhere():
    text = (
        "Ghostty is fast. UX and AX matter. See the ctx-rank branch and `kubectl`.\n"
        "## Voxtype Rocks"
    )
    assert dict(extract_terms(text, prose=True)) == {
        "kubectl": TermKind.NAME,
        "Ghostty": TermKind.NAME,
        "UX": TermKind.NAME,
        "Voxtype": TermKind.NAME,
        "ctx-rank": TermKind.IDENTIFIER,
    }


@pytest.mark.usefixtures("word_list")
def test_word_list_keeps_inflections_tags_contractions_ids_and_fragments_out():
    text = (
        "Configures it. Running it. Libraries break. Don't. WHERE </system-reminder> is "
        "toolu_01ABCDEF123 or call_abc123XYZ, commit 3f9a2b1c4d5e6f7a, id "
        "123e4567-e89b-12d3, token a8f3k2l9x0q1w7e5z4 in \u00dcnicode caf\u00e9 well-known text "
        "matching `*.qa.test.ts(x)` and `/clients/<id>/docs`."
    )
    assert extract_terms(text, prose=True) == []
    assert extract_terms("toolu_01ABCDEF123 call_abc123XYZ a8f3k2l9x0q1w7e5z4 sha256sum") == [
        ("sha256sum", TermKind.IDENTIFIER)
    ]


def test_directory_paths_are_terms_but_slashed_prose_is_not():
    text = (
        "Look in ~/projects/hark, ./build, /usr/share/dict and client/src/hark_client; "
        "and/or Hyprland/Omarchy at https://example.com/a/b/c on 2026/11/12."
    )
    paths = [t for t, k in extract_terms(text, prose=True) if k is TermKind.PATH]
    assert paths == ["~/projects/hark", "/usr/share/dict", "client/src/hark_client"]


def test_dictionary_terms_lead_the_shortlist_and_share_aliases():
    ctx = (
        Term("kubectl", ("kube control",), 0.7, TermKind.COMMAND, TermSource.TERMINAL_TEXT),
        Term("pods", ("pods",), 0.5, TermKind.WORD, TermSource.TERMINAL_TEXT),
    )
    words = (Term("kubectl", ("cube control",), 1.0, TermKind.WORD, TermSource.DICTIONARY),)
    merged = merge_terms(ctx, words, max_terms=2)
    assert [t.canonical for t in merged] == ["kubectl", "pods"]
    assert merged[0].spoken == ("cube control", "kube control")
    assert merged[0].source == TermSource.DICTIONARY
    assert merge_terms(ctx, words, max_terms=1) == merged[:1]
    assert merge_terms(ctx, (), max_terms=5) == ctx


def test_terminal_commands_take_free_slots_and_a_quarter_of_a_full_list():
    def ctx(n: int) -> tuple[Term, ...]:
        return tuple(
            Term(f"t{i}", (f"t {i}",), 0.5, TermKind.WORD, TermSource.REPO) for i in range(n)
        )

    git = Term("git", ("git",), 0.9, TermKind.COMMAND, TermSource.TERMINAL_TEXT)
    added = with_terminal_commands((git,), 30)
    assert [t.canonical for t in added] == list(TERMINAL_COMMANDS)  # git kept, not repeated
    assert added[0] is git
    assert "cube control" in added[TERMINAL_COMMANDS.index("kubectl")].spoken
    full = with_terminal_commands(ctx(30), 30)
    assert [t.canonical for t in full] == [*(f"t{i}" for i in range(23)), *TERMINAL_COMMANDS[:7]]
    assert len(with_terminal_commands(ctx(25), 30)) == 30  # 5 free slots, but 7 is the quarter
    assert with_terminal_commands(ctx(3), 0) == ()
