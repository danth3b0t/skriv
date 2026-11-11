"""tools/hark-probe end to end against the mock server."""

from __future__ import annotations

import importlib.util
import json
import wave
from pathlib import Path

from conftest import speech

PROBE = Path(__file__).resolve().parents[2] / "tools" / "hark-probe" / "hark_probe.py"


def load_probe():
    spec = importlib.util.spec_from_file_location("hark_probe", PROBE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def test_probe_streams_wav_with_bias_and_reports_latency(serve, tmp_path, capsys):
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

    final = json.loads(capsys.readouterr().out)
    assert final["n_best"][0]["text"] == "please run kubectl get"  # 5 x 200 ms windows
    data = json.loads(summary.read_text())
    assert len(data["runs"]) == 2
    assert data["runs"][0]["stream_s"] >= 0.95  # real-time pacing by default
    assert 0 < data["eoa_to_final_ms"]["p50"] < 1000
