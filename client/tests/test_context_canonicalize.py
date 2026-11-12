import pytest

from hark_client.context import Term, TermKind, TermSource, WordInfo, canonicalize
from hark_client.context.aliases import extract_terms
from hark_client.context.rank import Candidate, Origin, rank


def term(
    canonical: str, *spoken: str, weight: float = 0.8, kind: TermKind = TermKind.IDENTIFIER
) -> Term:
    return Term(canonical, spoken, weight, kind, TermSource.REPO_RECENT)


USE_STORE = term("useSnapshotStore", "use snapshot store")
STORE = term("SnapshotStore", "snapshot store")


def words(text: str, conf: dict[str, float]) -> list[WordInfo]:
    """One WordInfo per whitespace word; ``conf`` maps word -> confidence (default 0.95)."""
    return [
        WordInfo(w, i * 100, i * 100 + 90, conf.get(w, 0.95)) for i, w in enumerate(text.split())
    ]


def test_rewrites_alias_case_and_punctuation_insensitively():
    text, reps = canonicalize("Please fix Use Snapshot-Store.", [], [USE_STORE])
    assert text == "Please fix useSnapshotStore."
    assert [(r.before, r.after) for r in reps] == [("Use Snapshot-Store", "useSnapshotStore")]


def test_longest_alias_wins():
    text, _ = canonicalize("call use snapshot store now", [], [STORE, USE_STORE])
    assert text == "call useSnapshotStore now"


def test_match_does_not_cross_sentence_boundary():
    assert (
        canonicalize("I use snapshot. Store it", [], [USE_STORE])[0] == "I use snapshot. Store it"
    )


def test_match_does_not_split_a_written_word():
    assert (
        canonicalize("the snapshot store_v2 table", [], [STORE])[0] == "the snapshot store_v2 table"
    )


def test_low_confidence_word_blocks_replacement():
    text = "fix the snapshot store"
    out, reps = canonicalize(text, words(text, {"store": 0.3}), [STORE])
    assert (out, reps) == (text, [])


def test_confident_words_allow_replacement_and_reason_reports_confidence():
    text = "fix the snapshot store"
    out, reps = canonicalize(text, words(text, {"snapshot": 0.91}), [STORE])
    assert out == "fix the SnapshotStore"
    assert reps[0].reason == "alias match conf=0.91"


def test_plain_single_word_alias_is_ignored_but_mixed_case_one_applies():
    terms = [term("store", "store"), term("TypeScript", "typescript", kind=TermKind.NAME)]
    assert canonicalize("store it in typescript", [], terms)[0] == "store it in TypeScript"


def test_ambiguous_alias_is_skipped():
    terms = [term("snapshotStore", "snapshot store"), term("snapshot_store", "snapshot store")]
    assert canonicalize("the snapshot store", [], terms) == ("the snapshot store", [])


def test_higher_weight_wins_shared_alias():
    terms = [term("snapshotStore", "snapshot store", weight=0.5), STORE]
    assert canonicalize("the snapshot store", [], terms)[0] == "the SnapshotStore"


def test_offsets_are_utf8_byte_offsets():
    _, reps = canonicalize("café: snapshot store", [], [STORE])
    assert (reps[0].start, reps[0].end) == (7, 21)


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
    terms = rank(
        (Candidate(t, k, Origin.ASSISTANT) for t, k in extract_terms(reply, prose=True)), 60
    ).terms
    heard = "Run HarkDoctor, reopen terminal dot app; Qwen three ASR, the Nemo GPU PB parakeet."
    assert canonicalize(heard, [], terms)[0] == (
        "Run hark doctor, reopen Terminal.app; Qwen3-ASR, the NeMo GPU-PB parakeet."
    )


def test_rewrite_that_only_lowercases_is_skipped():
    doctor = term("hark doctor", "hark doctor", kind=TermKind.COMMAND)
    assert canonicalize("Hark doctor works.", [], [doctor]) == ("Hark doctor works.", [])


EACH_OTHER = term("each_other", "each other")
SESSION_START = term("SessionStart", "session start")


@pytest.mark.parametrize(
    ("heard", "written"),
    [
        ("we'll see each other soon", "we'll see each other soon"),
        ("we need a session start and end time", "we need a session start and end time"),
        ("Session start is slow.", "Session start is slow."),  # a sentence capital
        ("add a field called session start", "add a field called SessionStart"),
        ("the session start hook fires twice", "the SessionStart hook fires twice"),
        ("when session start is false", "when SessionStart is false"),
        ("we need a Session Start event", "we need a SessionStart event"),
        ("log each underscore other", "log each_other"),
    ],
)
def test_alias_of_everyday_words_needs_evidence(heard: str, written: str):
    assert canonicalize(heard, [], [EACH_OTHER, SESSION_START])[0] == written


def test_applied_bias_hit_on_the_span_is_evidence_for_everyday_words():
    """The recognizer's applied bias hit is the missing cue, but only for that term and only
    over the words it covers."""
    heard = "why does wait final time out"  # words at 0-90, 100-190, ...
    ws = words(heard, {})
    wait_final = term("wait_final", "wait final")
    assert canonicalize(heard, ws, [wait_final], hits=[("wait_final", 200, 390)])[0] == (
        "why does wait_final time out"
    )
    for hits in ([], [("each_other", 200, 390)], [("wait_final", 400, 590)]):
        assert canonicalize(heard, ws, [wait_final, EACH_OTHER], hits=hits)[0] == heard


@pytest.mark.parametrize(
    ("heard", "canonical", "kind", "written"),
    [
        ("keep S1 Mini as default", "S1-mini", TermKind.NAME, "keep S1-mini as default"),
        ("not switch to Quen 3 now", "Qwen3", TermKind.NAME, "not switch to Qwen3 now"),
        ("it is tdt zero point six b", "tdt-0.6b", TermKind.NAME, "it is tdt-0.6b"),
        ("it is tdt 0.6 B", "tdt-0.6b", TermKind.NAME, "it is tdt-0.6b"),
        ("bump to node 22 Alpine", "node:22-alpine", TermKind.NAME, "bump to node:22-alpine"),
        ("node twenty two alpine", "node:22-alpine", TermKind.NAME, "node:22-alpine"),
        ("it's not hyperland", "Hyprland", TermKind.NAME, "it's not Hyprland"),
        ("run Hyper-Control clients", "hyprctl", TermKind.NAME, "run hyprctl clients"),
        ("a ghosty keybind", "Ghostty", TermKind.NAME, "a Ghostty keybind"),
        ("read the source", "src", TermKind.NAME, "read the source"),  # an everyday word
        ("from tilde slash dot kube slash config", "~/.kube/config", TermKind.PATH,
         "from ~/.kube/config"),
        # part of a spoken path that did not match as a whole stays as said
        ("from slash dot kube slash config", "~/.kube/config", TermKind.PATH,
         "from slash dot kube slash config"),
        ("add it to Hark history list", "hark history", TermKind.COMMAND,
         "add it to hark history list"),
        # the recognizer's hyphen joins the alias to the next word
        ("tail it with journal control-user", "journalctl", TermKind.COMMAND,
         "tail it with journalctl-user"),
        ("journal control dash dash user", "journalctl", TermKind.COMMAND,
         "journalctl dash dash user"),
        ("keep journal control tight", "journalctl", TermKind.COMMAND,
         "keep journal control tight"),  # everyday words, no flag after the command
        ("a well-known fix", "known_fix", TermKind.IDENTIFIER, "a well-known fix"),
        # case-only: the recognizer's inner capital, never a sentence capital
        ("uv run pyTest-k gate", "pytest", TermKind.COMMAND, "uv run pytest-k gate"),
        ("PyTest is slow", "pytest", TermKind.COMMAND, "pytest is slow"),
        ("call model.Train now", "model.train()", TermKind.IDENTIFIER, "call model.train now"),
        ("Optimizer state", "optimizer", TermKind.NAME, "Optimizer state"),
        ("the API is down", "api", TermKind.NAME, "the API is down"),
        # spoken separators: one word may be a close mishearing, not two, not a far one
        ("set hark underscore models underscore diar to", "HARK_MODELS_DIR",
         TermKind.IDENTIFIER, "set HARK_MODELS_DIR to"),
        ("set hark underscore modes underscore diar to", "HARK_MODELS_DIR",
         TermKind.IDENTIFIER, "set hark underscore modes underscore diar to"),
        ("set hark underscore models underscore door to", "HARK_MODELS_DIR",
         TermKind.IDENTIFIER, "set hark underscore models underscore door to"),
        ("set models underscore diar to", "models_dir", TermKind.IDENTIFIER,
         "set models underscore diar to"),
    ],
)  # fmt: skip
def test_derived_spoken_forms_match(heard: str, canonical: str, kind: TermKind, written: str):
    """Only the derived spoken forms (none on the term): lexicon, numbers, prefixes."""
    assert canonicalize(heard, [], [term(canonical, kind=kind)])[0] == written
