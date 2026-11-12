"""Model routing by SessionStart.languages, lazy loading, and the language fallback."""

from __future__ import annotations

from typing import TYPE_CHECKING

import grpc
import pytest
from conftest import kubectl_term, speech, utterance

from skriv.v1 import skriv_pb2 as pb
from skriv_server import session
from skriv_server.backends.mock import MockBackend
from skriv_server.language import implausible

if TYPE_CHECKING:
    from conftest import Serve


def models(multi_script: list[str], *, english_load_ms: int = 0) -> list[MockBackend]:
    """A default model that "detects" en/cs/ru and writes `multi_script`, and an English-only
    model (loaded on first use) that writes "cube control"."""
    return [
        MockBackend("multi", languages=["en", "cs", "ru"], script=multi_script),
        MockBackend("english", script=["cube", "control"], load_ms=english_load_ms),
    ]


async def test_capabilities_list_both_models_with_their_languages(serve: Serve):
    client = await serve(backend=models(["ahoj"]))
    caps = await client.asr.GetCapabilities(pb.GetCapabilitiesRequest())
    assert [(m.model_id, list(m.languages), m.language_detection, m.is_default, m.state)
            for m in caps.models] == [
        ("multi", ["en", "cs", "ru"], True, True, pb.MODEL_STATE_READY),
        ("english", ["en"], False, False, pb.MODEL_STATE_COLD),
    ]  # fmt: skip


@pytest.mark.parametrize(
    ("languages", "model_id", "model"),
    [
        (["en"], "", "english"),
        (["en-GB"], "", "english"),
        (["en", "cs"], "", "multi"),  # only the multilingual model covers both
        (["cs"], "", "multi"),
        ([], "", "multi"),  # auto: the default detects
        (["en"], "multi", "multi"),  # an explicit model wins
    ],
)
async def test_sessions_go_to_the_narrowest_model_that_covers_their_languages(
    serve: Serve, languages: list[str], model_id: str, model: str
):
    client = await serve(backend=models(["ahoj"]))
    out = await client.transcribe(utterance(speech(400), languages=languages, model_id=model_id))
    assert out.of("accepted")[0].model_id == out.final.model_id == model
    assert out.final.n_best[0].text == ("cube control" if model == "english" else "ahoj ahoj")
    # A single-language model reports its language; a detecting mock cannot say.
    assert out.final.detected_language == ("en" if model == "english" else "")


async def test_second_model_loads_on_first_use_and_stays(serve: Serve):
    client = await serve(backend=models(["ahoj"], english_load_ms=50))
    for _ in range(2):
        out = await client.transcribe(utterance(speech(400), languages=["en"]))
        assert out.final.model_id == "english"
    caps = await client.asr.GetCapabilities(pb.GetCapabilitiesRequest())
    assert [m.state for m in caps.models] == [pb.MODEL_STATE_READY] * 2


async def test_a_slow_first_load_does_not_hold_the_session(
    serve: Serve, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(session, "LAZY_LOAD_WAIT_S", 0.05)
    client = await serve(backend=models(["ahoj"], english_load_ms=400))
    first = await client.transcribe(utterance(speech(400), languages=["en"]))
    assert first.final.model_id == "multi"  # the default meanwhile
    explicit = await client.transcribe(utterance(speech(400), model_id="english"))
    assert explicit.code == grpc.StatusCode.UNAVAILABLE  # asked for by name: not swapped
    assert explicit.error is not None
    assert explicit.error.code == pb.ERROR_CODE_MODEL_NOT_READY
    monkeypatch.setattr(session, "LAZY_LOAD_WAIT_S", 2.0)
    later = await client.transcribe(utterance(speech(400), languages=["en"]))
    assert later.final.model_id == "english"


@pytest.mark.parametrize(
    ("script", "languages", "model"),
    [
        (["Гри"], ["en", "cs"], "english"),  # Cyrillic: no allowed language writes it
        (["sí"], ["en", "cs"], "english"),  # a Spanish one-word reply
        (["Гри"], ["en", "ru"], "multi"),  # Cyrillic is allowed
        (["Гри"], ["cs"], "multi"),  # no English allowed: nothing to fall back to
        (["Гри"], [], "multi"),  # auto: anything goes
        (["Číslo"], ["en", "cs"], "multi"),  # Czech
    ],
)
async def test_implausible_output_is_decoded_again_by_the_english_model(
    serve: Serve, script: list[str], languages: list[str], model: str
):
    client = await serve(backend=models(script))
    ctx = pb.RecognitionContext(revision=1, bias=pb.BiasList(terms=[kubectl_term()]))
    out = await client.transcribe(utterance(speech(400), languages=languages, context=ctx))
    final = out.final
    assert out.of("accepted")[0].model_id == "multi"
    assert final.model_id == model
    fallbacks = [w for w in out.of("warning") if w.code == pb.WARNING_CODE_LANGUAGE_FALLBACK]
    if model == "english":
        # biased with a plan the English model made, its own anchor
        assert (final.n_best[0].text, final.anchor.text) == ("kubectl", "cube control")
        assert [h.term_id for h in final.bias_hits] == ["t-kubectl"]
        assert "language_fallback" in final.timings.backend_ms
        (warning,) = fallbacks
        assert warning.message.startswith(("script:CYRILLIC", "short:es"))
        assert script[0] not in warning.message  # never user text
    else:
        assert final.n_best[0].text == f"{script[0]} {script[0]}"
        assert not fallbacks
        assert "language_fallback" not in final.timings.backend_ms


@pytest.mark.parametrize(
    ("text", "allowed", "reason"),
    [
        ("Гри", ["en", "cs"], "script:CYRILLIC"),
        ("Γεια σου", ["en"], "script:GREEK"),  # noqa: RUF001 - Greek on purpose
        ("Привет", ["en", "ru-RU"], ""),
        ("Петер Пред", ["cs"], "script:CYRILLIC"),
        ("sí.", ["en", "cs"], "short:es"),
        ("Hej.", ["en", "cs"], "short:sk"),  # also Slovak, Polish, Swedish, Danish...
        ("Hej.", ["en", "sv"], ""),
        ("Ja, okay.", ["en"], ""),  # "okay" is English: mixed, not clearly foreign
        ("Číslo", ["en", "cs"], ""),  # Latin script, not a listed reply
        ("Ano.", ["en", "cs"], ""),
        ("Hola amigos, we should go now", ["en"], ""),  # long: not judged by words
        ("Tokyo 東京", ["en", "ja"], ""),  # ja: script unknown here, not judged
        ("Hej", [], ""),  # auto
        ("", ["en"], ""),
    ],
)
def test_plausibility_for_allowed_languages(text: str, allowed: list[str], reason: str):
    assert implausible(text, allowed) == reason
