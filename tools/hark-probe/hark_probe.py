"""hark-probe: stream a WAV file to a hark.v1 server and report the result.

Prints the FinalResult (protobuf JSON) on stdout and a latency breakdown on
stderr. "eoa->final" is client-side: from handing EndOfAudio to gRPC until the
FinalResult arrives (key-up -> final on a real client, minus capture/paste).

Runs inside the server's uv project (see the `hark-probe` wrapper script).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
import uuid
import wave
from pathlib import Path
from typing import TYPE_CHECKING, TypedDict, cast

import grpc
from google.protobuf import json_format

from hark.v1 import hark_pb2 as pb
from hark.v1 import hark_pb2_grpc as pb_grpc

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from hark.v1.hark_pb2_grpc import AsrAsyncStub

SAMPLE_RATE = 16000


class Args(argparse.Namespace):
    # Annotations only: argparse fills in the defaults for attributes that are not set.
    wav: str
    server: str
    fast: bool
    bias: str | None
    model: str
    language: str
    chunk_ms: int
    budget_ms: int
    anchor: str
    n_best: int
    words: bool
    repeat: int
    warmup: bool
    caps: bool
    json_summary: str | None
    quiet: bool
    tls_ca: str | None
    tls_cert: str | None
    tls_key: str | None
    tls_server_name: str | None


class RunResult(TypedDict):
    audio_s: float
    accepted_ms: float
    stream_s: float
    eoa_to_final_ms: float
    server_end_to_final_ms: int
    network_client_ms: float
    encoder_ms: int
    decode_ms: int
    queue_ms: int
    eager_hit: bool
    text: str
    warnings: list[dict[str, object]]
    final: pb.FinalResult


def read_wav(path: str) -> bytes:
    with wave.open(path, "rb") as w:
        if (w.getframerate(), w.getnchannels(), w.getsampwidth()) != (SAMPLE_RATE, 1, 2):
            raise SystemExit(
                f"{path}: need 16 kHz mono 16-bit PCM WAV "
                f"(got {w.getframerate()} Hz, {w.getnchannels()} ch, {8 * w.getsampwidth()} bit); "
                "convert with: afconvert -f WAVE -d LEI16@16000 -c 1 in.aiff out.wav"
            )
        return w.readframes(w.getnframes())


def load_bias(path: str) -> pb.BiasList:
    """BiasList in protobuf JSON; aliases may be plain strings.
    e.g. {"terms": [{"id": "t1", "canonical": "kubectl", "aliases": ["cube control"], "weight": 0.8}]}"""
    raw = cast("object", json.loads(Path(path).read_text()))
    if isinstance(raw, list):
        raw = {"terms": cast("list[object]", raw)}
    data = cast("dict[str, list[dict[str, object]]]", raw)
    for term in data.get("terms", []):
        aliases = cast("list[object]", term.get("aliases", []))
        term["aliases"] = [{"text": a} if isinstance(a, str) else a for a in aliases]
    return json_format.ParseDict(data, pb.BiasList())


def channel(args: Args) -> grpc.aio.Channel:
    if not args.tls_ca:
        return grpc.aio.insecure_channel(args.server)

    def read(p: str) -> bytes:
        return Path(p).read_bytes()

    creds = grpc.ssl_channel_credentials(
        root_certificates=read(args.tls_ca),
        private_key=read(args.tls_key) if args.tls_key else None,
        certificate_chain=read(args.tls_cert) if args.tls_cert else None,
    )
    options = (
        [("grpc.ssl_target_name_override", args.tls_server_name)] if args.tls_server_name else []
    )
    return grpc.aio.secure_channel(args.server, creds, options=options)


async def run_once(
    stub: AsrAsyncStub, args: Args, pcm: bytes, bias: pb.BiasList | None
) -> RunResult:
    chunk_bytes = SAMPLE_RATE * args.chunk_ms // 1000 * 2
    chunks = [pcm[i : i + chunk_bytes] for i in range(0, len(pcm), chunk_bytes)]
    start = pb.SessionStart(
        utterance_id=str(uuid.uuid4()),
        model_id=args.model,
        languages=[args.language] if args.language else [],
        audio_format=pb.AudioFormat(
            encoding=pb.AUDIO_ENCODING_PCM_S16LE, sample_rate_hz=SAMPLE_RATE, channels=1
        ),
        options=pb.ResultOptions(
            word_timestamps=args.words,
            word_confidence=args.words,
            n_best=args.n_best,
            anchor=pb.AnchorMode.Value(f"ANCHOR_MODE_{args.anchor.upper()}"),
        ),
        deadlines=pb.Deadlines(finalize_budget_ms=args.budget_ms),
        client=pb.ClientInfo(client_version="hark-probe/0.1"),
    )
    if bias is not None:
        start.context.CopyFrom(pb.RecognitionContext(revision=1, bias=bias))
    t: dict[str, float] = {}

    async def requests() -> AsyncIterator[pb.TranscribeRequest]:
        t["start"] = time.perf_counter()
        yield pb.TranscribeRequest(start=start)
        sent = 0
        for seq, data in enumerate(chunks):
            n = len(data) // 2
            if not args.fast:  # chunk i is "captured" at (i+1) * chunk_ms
                due = t["start"] + (sent + n) / SAMPLE_RATE
                await asyncio.sleep(max(0.0, due - time.perf_counter()))
            yield pb.TranscribeRequest(
                audio=pb.AudioChunk(data=data, seq=seq, start_sample=sent, num_samples=n)
            )
            sent += n
        t["eoa"] = time.perf_counter()
        yield pb.TranscribeRequest(
            end_of_audio=pb.EndOfAudio(
                last_seq=max(len(chunks) - 1, 0), total_samples=sent, reason=pb.END_REASON_KEY_UP
            )
        )

    call = stub.Transcribe(requests())
    final: pb.FinalResult | None = None
    ended: pb.SessionEnded | None = None
    warnings: list[dict[str, object]] = []
    async for msg in call:
        kind = msg.WhichOneof("msg")
        if kind == "accepted":
            t["accepted"] = time.perf_counter()
        elif kind == "warning":
            warnings.append(
                json_format.MessageToDict(msg.warning, preserving_proto_field_name=True)
            )
        elif kind == "final_result":
            t["final"] = time.perf_counter()
            final = msg.final_result
        elif kind == "session_ended":
            ended = msg.session_ended
    if ended is not None or final is None:
        detail = json_format.MessageToJson(ended, preserving_proto_field_name=True) if ended else ""
        raise RuntimeError(f"session ended without result: {detail or await call.code()}")

    def ms(a: str, b: str) -> float:
        return (t[b] - t[a]) * 1000

    server_ms = final.timings.end_to_final_ms
    return {
        "audio_s": len(pcm) / 2 / SAMPLE_RATE,
        "accepted_ms": ms("start", "accepted"),
        "stream_s": ms("start", "eoa") / 1000,
        "eoa_to_final_ms": ms("eoa", "final"),
        "server_end_to_final_ms": server_ms,
        "network_client_ms": ms("eoa", "final") - server_ms,
        "encoder_ms": final.timings.encoder_ms,
        "decode_ms": final.timings.decode_ms,
        "queue_ms": final.timings.queue_ms,
        "eager_hit": final.timings.eager_hit,
        "text": final.n_best[0].text,
        "warnings": warnings,
        "final": final,
    }


def pct(values: list[float], q: float) -> float:
    s = sorted(values)
    return s[min(len(s) - 1, max(0, round(q * (len(s) - 1))))]


async def main_async(args: Args) -> int:
    pcm = read_wav(args.wav)
    bias = load_bias(args.bias) if args.bias else None
    async with channel(args) as ch:
        stub = pb_grpc.AsrStub(ch)
        if args.caps:
            caps = await stub.GetCapabilities(
                pb.GetCapabilitiesRequest(client_version="hark-probe/0.1")
            )
            print(json_format.MessageToJson(caps, preserving_proto_field_name=True))
            return 0
        summary: dict[str, object] = {"wav": args.wav, "fast": args.fast, "runs": []}
        if args.warmup:
            t0 = time.perf_counter()
            w = await stub.Warmup(pb.WarmupRequest(model_id=args.model))
            rpc_ms = (time.perf_counter() - t0) * 1000
            summary["warmup"] = {"server_warm_ms": w.warm_ms, "rpc_ms": rpc_ms}
            print(f"warmup: server {w.warm_ms} ms, rpc {rpc_ms:.0f} ms", file=sys.stderr)
        results: list[RunResult] = []
        for i in range(args.repeat):
            try:
                r = await run_once(stub, args, pcm, bias)
            except (RuntimeError, grpc.aio.AioRpcError) as e:
                print(f"run {i + 1}: {e}", file=sys.stderr)
                return 1
            results.append(r)
            if not args.quiet:
                print(
                    f"run {i + 1}/{args.repeat}: audio {r['audio_s']:.2f} s, streamed {r['stream_s']:.2f} s, "
                    f"accepted {r['accepted_ms']:.1f} ms, eoa->final {r['eoa_to_final_ms']:.1f} ms "
                    f"(server {r['server_end_to_final_ms']} ms [encoder {r['encoder_ms']}, "
                    f"decode {r['decode_ms']}, queue {r['queue_ms']}], network+client "
                    f"{r['network_client_ms']:.1f} ms){' eager' if r['eager_hit'] else ''}",
                    file=sys.stderr,
                )
        e2f = [r["eoa_to_final_ms"] for r in results]
        srv = [r["server_end_to_final_ms"] for r in results]
        e2f_stats = {
            "p50": statistics.median(e2f),
            "p95": pct(e2f, 0.95),
            "min": min(e2f),
            "max": max(e2f),
        }
        srv_p50 = statistics.median(srv)
        summary["runs"] = [{k: v for k, v in r.items() if k != "final"} for r in results]
        summary["eoa_to_final_ms"] = e2f_stats
        summary["server_end_to_final_ms_p50"] = srv_p50
        print(
            f"eoa->final over {len(e2f)} runs: p50 {e2f_stats['p50']:.1f} ms, "
            f"p95 {e2f_stats['p95']:.1f} ms, min {min(e2f):.1f}, max {max(e2f):.1f} "
            f"(server p50 {srv_p50:.0f} ms)",
            file=sys.stderr,
        )
        print(json_format.MessageToJson(results[-1]["final"], preserving_proto_field_name=True))
        if args.json_summary:
            text = json.dumps(summary, indent=2)
            await asyncio.to_thread(Path(args.json_summary).write_text, text)
    return 0


def parse_args(argv: list[str] | None = None) -> Args:
    # __doc__ is None under `python -OO`.
    description = (__doc__ or "").split("\n\n")[0]
    p = argparse.ArgumentParser(prog="hark-probe", description=description)
    p.add_argument("wav", help="16 kHz mono PCM16 WAV")
    p.add_argument("--server", default="127.0.0.1:50051")
    p.add_argument(
        "--fast", action="store_true", help="send audio as fast as possible (default: real time)"
    )
    p.add_argument("--bias", help="BiasList JSON file")
    p.add_argument("--model", default="", help="model_id ('' = server default)")
    p.add_argument("--language", default="")
    p.add_argument("--chunk-ms", type=int, default=40)
    p.add_argument(
        "--budget-ms", type=int, default=0, help="finalize_budget_ms (0 = server default)"
    )
    p.add_argument("--anchor", default="if_cheap", choices=["off", "if_cheap", "always"])
    p.add_argument("--n-best", type=int, default=0)
    p.add_argument("--words", action="store_true", help="request word timestamps + confidence")
    p.add_argument("--repeat", type=int, default=1)
    p.add_argument("--warmup", action="store_true", help="call Warmup first and report its time")
    p.add_argument("--caps", action="store_true", help="print GetCapabilities and exit")
    p.add_argument("--json-summary", help="write per-run latencies + p50/p95 here")
    p.add_argument("--quiet", action="store_true")
    p.add_argument("--tls-ca", help="CA bundle (enables TLS)")
    p.add_argument("--tls-cert", help="client cert (mTLS)")
    p.add_argument("--tls-key", help="client key (mTLS)")
    p.add_argument("--tls-server-name", help="override expected server name")
    return p.parse_args(argv, namespace=Args())


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(main_async(parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
