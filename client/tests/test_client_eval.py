"""`skriv eval`: scoring rules, and add -> run -> accept against the in-process mock server."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, cast

import pytest

from skriv_client import evalset, history, retry
from skriv_client.__main__ import main

from .conftest import pcm, snapshot

if TYPE_CHECKING:
    from pathlib import Path

    from .conftest import FakeContext, SkrivServer


NONE: list[tuple[str, str]] = []
NO_STR: list[str] = []


@pytest.mark.parametrize(
    ("label", "canonical", "final", "expected"),
    [
        (  # everything there; a new wording is not a failure, only unreviewed
            evalset.Label("um run kubectl get pods", critical=["kubectl"]),
            "run kubectl get pods", "Run kubectl get pods.",
            (0.0, NONE, NO_STR, False, False, False),
        ),
        (  # recognized right, lost by cleanup; a false substitution
            evalset.Label("run kubectl in the hyper land", critical=["kubectl"], forbidden=["Hyprland"]),
            "run kubectl in the Hyprland", "Run it in the Hyprland.",
            (0.0, [("kubectl", "cleanup")], ["Hyprland"], False, False, True),
        ),
        (  # a negation dropped: fails until a human accepts that exact text
            evalset.Label("don't push it", accepted=["Push it."]),
            "don't push it", "Push it.",
            (0.0, NONE, NO_STR, True, True, False),
        ),
        (  # the "no" of a resolved self-correction is no lost negation
            evalset.Label("make it ten megabytes, no, twenty megabytes"),
            "make it 10 megabytes, no, 20 megabytes", "Make it 20 megabytes.",
            (0.0, NONE, NO_STR, False, False, False),
        ),
        (
            evalset.Label("don't push it", critical=["--force"]),
            "don't push it", "Don't push it.",
            (0.0, [("--force", "recognition")], NO_STR, False, False, True),
        ),
    ],
)  # fmt: skip
def test_score(
    label: evalset.Label,
    canonical: str,
    final: str,
    expected: tuple[float, list[tuple[str, str]], list[str], bool, bool, bool],
):
    s = evalset.score("c", label, label.said, canonical, final)
    assert (s.wer, s.missing, s.forbidden, s.negations_lost, s.accepted, s.failed) == expected


def test_wer_counts_substitutions_insertions_and_deletions():
    assert evalset.wer("run the tests now", "Run the test, now!") == 0.25
    assert evalset.wer("a b c", "a x b c d") == pytest.approx(2 / 3)
    assert evalset.wer("", "") == 0.0


def test_add_run_accept(
    tmp_path: Path,
    skriv_server: SkrivServer,
    fake_context: FakeContext,
    capsys: pytest.CaptureFixture[str],
):
    data = tmp_path / "data"
    cfg = tmp_path / "config.toml"
    cfg.write_text(f'[history]\ndir = "{data}"\n[cleanup]\nenabled = false\n')
    uid = "0199aaaa-0000-7000-8000-000000000001"
    # a kept dictation (1 s = "please run cube control get") with the kubectl term at key-down
    retry.save(
        data, 5, pcm(1.0), snapshot("com.apple.Notes"), {"utterance_id": uid, "app": "Notes"}
    )
    history.append(data, {"utterance_id": uid, "raw_text": "please run kubectl get",
                          "replacements": [{"term": "kubectl"}]})  # fmt: skip
    argv = ["--config", str(cfg), "--server", skriv_server.target, "eval"]

    assert main([*argv, "add"]) == 0
    label_path = data / "eval" / uid / evalset.LABEL
    label = cast("dict[str, object]", json.loads(label_path.read_text()))
    assert (label["said"], label["critical"]) == ("please run kubectl get", ["kubectl"])
    assert main([*argv, "add"]) == 1  # already in the set
    label["said"] = "please run cube control get"  # corrected by hand: what was said
    label_path.write_text(json.dumps(label))

    capsys.readouterr()
    assert main([*argv, "run"]) == 0
    out = capsys.readouterr().out
    # biased: "kubectl" for "cube control", one substitution and one deletion of five words
    assert "new  00000001  pasted   wer 40%" in out
    assert "1 clips: 0 failed, 1 unreviewed" in out
    assert main([*argv, "accept", uid[-8:]]) == 0
    assert json.loads(label_path.read_text())["accepted"] == ["please run kubectl get"]
    assert main([*argv, "run"]) == 0
    assert "ok   00000001" in capsys.readouterr().out

    label = cast("dict[str, object]", json.loads(label_path.read_text()))
    label["forbidden"] = ["kubectl get"]
    label_path.write_text(json.dumps(label))
    assert main([*argv, "run"]) == 1
    assert "forbidden 'kubectl get'" in capsys.readouterr().out

    # ablation without context: the stored shortlist is cut to max_terms = 0, so nothing is
    # biased or canonicalized; --report has every stage
    cfg.write_text(cfg.read_text() + "[context]\nmax_terms = 0\n")
    report = tmp_path / "report.json"
    assert main([*argv, "run", "--report", str(report)]) == 1
    (clip,) = cast("list[dict[str, object]]", json.loads(report.read_text()))
    assert (clip["clip"], clip["raw"], clip["final"]) == (
        uid,
        "please run cube control get",
        "please run cube control get",
    )
    assert (clip["replacements"], clip["gate"]) == ([], {"status": "no_terms"})
    assert clip["missing"] == [{"string": "kubectl", "stage": "recognition"}]
