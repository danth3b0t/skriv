from hark_client.context import Term, TermKind, TermSource, WordInfo, canonicalize
from hark_client.context.aliases import extract_terms
from hark_client.context.rank import Candidate, ranked


def term(
    canonical: str, *spoken: str, weight: float = 0.8, kind: TermKind = TermKind.IDENTIFIER
) -> Term:
    return Term(canonical, spoken, weight, kind, TermSource.REPO_RECENT)


USE_STORE = term("useSessionStore", "use session store")
STORE = term("SessionStore", "session store")


def words(text: str, conf: dict[str, float]) -> list[WordInfo]:
    """One WordInfo per whitespace word; ``conf`` maps word -> confidence (default 0.95)."""
    return [
        WordInfo(w, i * 100, i * 100 + 90, conf.get(w, 0.95)) for i, w in enumerate(text.split())
    ]


def test_rewrites_alias_case_and_punctuation_insensitively():
    text, reps = canonicalize("Please fix Use Session-Store.", [], [USE_STORE])
    assert text == "Please fix useSessionStore."
    assert [(r.before, r.after) for r in reps] == [("Use Session-Store", "useSessionStore")]


def test_longest_alias_wins():
    text, _ = canonicalize("call use session store now", [], [STORE, USE_STORE])
    assert text == "call useSessionStore now"


def test_match_does_not_cross_sentence_boundary():
    assert canonicalize("I use session. Store it", [], [USE_STORE])[0] == "I use session. Store it"


def test_match_does_not_split_a_written_word():
    assert (
        canonicalize("the session store_v2 table", [], [STORE])[0] == "the session store_v2 table"
    )


def test_low_confidence_word_blocks_replacement():
    text = "fix the session store"
    out, reps = canonicalize(text, words(text, {"store": 0.3}), [STORE])
    assert (out, reps) == (text, [])


def test_confident_words_allow_replacement_and_reason_reports_confidence():
    text = "fix the session store"
    out, reps = canonicalize(text, words(text, {"session": 0.91}), [STORE])
    assert out == "fix the SessionStore"
    assert reps[0].reason == "alias match conf=0.91"


def test_plain_single_word_alias_is_ignored_but_mixed_case_one_applies():
    terms = [term("store", "store"), term("TypeScript", "typescript", kind=TermKind.NAME)]
    assert canonicalize("store it in typescript", [], terms)[0] == "store it in TypeScript"


def test_ambiguous_alias_is_skipped():
    terms = [term("sessionStore", "session store"), term("session_store", "session store")]
    assert canonicalize("the session store", [], terms) == ("the session store", [])


def test_higher_weight_wins_shared_alias():
    terms = [term("sessionStore", "session store", weight=0.5), STORE]
    assert canonicalize("the session store", [], terms)[0] == "the SessionStore"


def test_offsets_are_utf8_byte_offsets():
    _, reps = canonicalize("café: session store", [], [STORE])
    assert (reps[0].start, reps[0].end) == (7, 20)


def test_already_canonical_text_reports_no_replacement():
    ts = term("TypeScript", "typescript", kind=TermKind.NAME)
    assert canonicalize("in TypeScript", [], [ts]) == ("in TypeScript", [])


def test_spoken_path_is_rewritten():
    out, _ = canonicalize("open store dot ts please", [], [term("store.ts", "store dot ts")])
    assert out == "open store.ts please"


def test_names_from_an_agent_reply_fix_the_real_misrecognitions():
    reply = (
        "Compare Qwen3-ASR with Parakeet and the NeMo GPU-PB backend. "
        "Run `hark doctor` and reopen Terminal.app."
    )
    terms = ranked(
        (Candidate(t, k, TermSource.AGENT_SESSION) for t, k in extract_terms(reply, prose=True)),
        60,
    )
    heard = "Run HarkDoctor, reopen terminal dot app; Qwen three ASR, the Nemo GPU PB parakeet."
    assert canonicalize(heard, [], terms)[0] == (
        "Run hark doctor, reopen Terminal.app; Qwen3-ASR, the NeMo GPU-PB parakeet."
    )


def test_rewrite_that_only_lowercases_is_skipped():
    doctor = term("hark doctor", "hark doctor", kind=TermKind.COMMAND)
    assert canonicalize("Hark doctor works.", [], [doctor]) == ("Hark doctor works.", [])
