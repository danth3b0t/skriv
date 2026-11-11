"""tools/hark-probe end to end against the mock server."""

from __future__ import annotations

import importlib.util
import json
import wave
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, cast

from conftest import dig, speech

if TYPE_CHECKING:
    import argparse

    import pytest
    from conftest import Serve

PROBE = Path(__file__).resolve().parents[2] / "tools" / "hark-probe" / "hark_probe.py"


class Probe(Protocol):
    """The part of tools/hark-probe/hark_probe.py used here."""

    def parse_args(self, argv: list[str] | None = None) -> argparse.Namespace: ...
    async def main_async(self, args: argparse.Namespace) -> int: ...


def load_probe() -> Probe:
    spec = importlib.util.spec_from_file_location("hark_probe", PROBE)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return cast("Probe", module)


async def test_probe_streams_wav_with_bias_and_reports_latency(
    serve: Serve, tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    client = await serve()
    wav_path = tmp_path / "a.wav"
    with wave.open(str(wav_path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(speech(1000).astype("<i2").tobytes())
    bias = tmp_path / "bias.json"
    bias.write_text(json.dumps([{"id": "k", "canonical": "kubectl", "aliases": ["cube control"]}]))
    summary = tmp_path / "summary.json"

    probe = load_probe()
    args = probe.parse_args(
        [str(wav_path), "--server", client.target, "--bias", str(bias), "--repeat", "2",
         "--words", "--json-summary", str(summary)]
    )  # fmt: skip
    assert await probe.main_async(args) == 0

    final = cast("object", json.loads(capsys.readouterr().out))
    assert dig(final, "n_best", 0, "text") == "please run kubectl get"  # 5 x 200 ms windows
    data = cast("object", json.loads(summary.read_text()))
    runs = cast("list[dict[str, float]]", dig(data, "runs"))
    assert len(runs) == 2
    assert runs[0]["stream_s"] >= 0.95  # real-time pacing by default
    assert 0 < cast("float", dig(data, "eoa_to_final_ms", "p50")) < 1000
