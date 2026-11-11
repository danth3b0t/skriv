# Bench: hark-server + parakeet_mlx on the Mac (2026-11-11)

> **These are macOS `say` TTS clips, not real speech.** They are clean, evenly paced and synthetic. They show
> server latency and its shape; they say nothing about accuracy. The dataset spec (§6.3) keeps TTS out of dev/test,
> and these numbers should not be quoted as dictation latency without a re-run on real recordings.

## Setup

| | |
|---|---|
| Host | MacBook, Apple M5 Pro, 48 GB, macOS 26.5.1. Other apps were running (browser etc.); load average is given per pass. |
| Server | `hark-server --backend parakeet_mlx`: `mlx-community/parakeet-tdt-0.6b-v3`, bfloat16, greedy TDT decode, `STREAMING_MODE_BUFFERED` (all decoding happens after EndOfAudio), bias mechanism NONE, anchor == primary (FREE). |
| Versions | Python 3.12.15 (uv), parakeet-mlx 0.7.0, mlx 0.32.4, grpcio 1.85.1 |
| Client | `tools/hark-probe`, same host, loopback plaintext gRPC. 40 ms PCM16 chunks, **real-time pacing** (chunk *i* is sent at (*i*+1)·40 ms), then EndOfAudio right after the last chunk. Requests word timestamps + confidence and sends a 4-term bias list (`kubectl`, `PyTorch`, `Hyprland`, `gRPC`). The decoder ignores the list; the server uses it only for hit detection and warns `CONTEXT_IGNORED`. |
| Metric | **eoa→final** is measured on the client: from handing EndOfAudio to gRPC until FinalResult arrives. That is key-up→final minus capture and paste. The server-side `end_to_final_ms` is also listed; the difference is loopback plus client overhead, 1–5 ms. |

Clips (voice = system default; durations after conversion):

```sh
say -o short.aiff  "Please run kubectl get pods in the default namespace."                       # 3.10 s
say -o medium.aiff "Open the hark repository, check the parakeet backend, and make sure the gRPC server returns word timestamps and the unbiased anchor with every final result."   # 10.41 s
say -o long.aiff   "Please refactor the session module so that the finalize pipeline sheds the N best list before the anchor when the budget is tight. Then add a test for the eager end path, run the whole suite with pytest, and commit the change with a short message that mentions PyTorch, Hyprland and the Tailscale network on the home desktop with the RTX 3060."   # 20.52 s
for f in short medium long; do afconvert -f WAVE -d LEI16@16000 -c 1 $f.aiff $f.wav; done
```

## Results: eoa→final, 5 runs per clip (ms)

| Clip | Pass 1, real time (load avg ~9–12) | Pass 2, real time (load avg ~3–6) | `--fast` (no idle gap) |
|---|---|---|---|
| 3.1 s | **p50 106** (105, 111, 106, 105, 109) | **p50 86** (93, 79, 86, 86, 107) | p50 64 |
| 10.4 s | **p50 137** (206, 146, 137, 120, 122) | **p50 134** (116, 134, 122, 146, 145) | p50 114 |
| 20.5 s | **p50 260** (226, 662, 260, 472, 237) | **p50 243** (231, 246, 246, 243, 215) | p50 227 |

Typical server-side breakdown, pass 2:

| Clip | encoder (mel + Conformer) | greedy TDT decode | queue |
|---|---|---|---|
| 3.1 s | 46–72 | 31–34 | 0 |
| 10.4 s | 51–76 | 62–69 | 0 |
| 20.5 s | 64–93 | 149–158 | 0 |

With `--fast` the encoder takes 29 / 39 / 54 ms. Decode time does not change.

## Cold start / warmup

| What | Time |
|---|---|
| Process launch (`uv run hark-server`) → gRPC health `SERVING`, warmup on start (3 runs) | 1.06 s, 1.20 s, 1.22 s |
| …of which model load (`from_pretrained`, weights in HF cache and OS page cache) | 560, 436, 434 ms |
| …of which synthetic warmup decodes (1 s + 10 s of noise) | 288, 202, 194 ms |
| `Warmup` RPC on a server started with `--no-warmup` (load + warmup decodes) | 605 ms |
| First real request after that Warmup (3.1 s clip) | 100 ms eoa→final (steady state: 86–106) |
| First-ever load, including the HF download of the weights (one run) | 54 s |

Before Warmup, a session on a `--no-warmup` server is rejected with `UNAVAILABLE` / `MODEL_NOT_READY` (retryable), as designed.
The OS page cache was not purged (that needs sudo), so a load after a reboot will be slower than the numbers above.

## Observations

1. **Finalize grows with utterance length because the server is BUFFERED.** The greedy TDT decode is a per-frame Python loop of small MLX ops: about 6–10 ms per audio second (31 → 150 ms from 3 s to 20 s). The encoder is 30–90 ms. Both run after key-up. Even so, the p50 is ≤ 260 ms for 20 s, well inside the 1 s key-up→paste budget on the Mac.
2. **Idle GPU costs about 20–40 ms.** At real-time pacing the GPU sits idle while audio streams in, and the encoder then takes 1.5–2× its back-to-back time. A likely cause is GPU clock ramp-down; this was not verified.
3. **Tails come from contention.** Pass 1 ran with load average ~12 from other apps and had 472/662 ms outliers on the 20 s clip, mostly in decode. Pass 2 had none. Five runs are too few for a p95; the per-run values are listed instead.
4. **The protocol costs nothing measurable.** `accepted` arrives 0.6–1.5 ms after `start`. Client minus server time is 1–5 ms on loopback.
5. **Accuracy is out of scope here (TTS), but bias matters.** Without bias the model writes "cube git" for kubectl, "Hipperland" for Hyprland, "PyDest" for pytest and "RDX" for RTX. With mechanism NONE the only bias hits are `ALREADY_IN_ANCHOR` (`gRPC` matched "GRPC" case-insensitively; `PyTorch`).

## Next steps suggested by these numbers

- **INCREMENTAL mode** (`model.transcribe_stream()`), so encoding and decoding happen while the user speaks. It removes most of observations 1 and 2 from the critical path. Then measure partial-vs-final drift.
- **Decode-time bias on MLX.** parakeet-mlx 0.7 has word boosting, but only in beam search (`Beam(booster=boost.words(...))`). Its latency and false-substitution cost must be measured before the backend advertises `DECODE_TIME_BOOST`. With beam decoding the greedy anchor would become a second decode.
- Re-run on real recordings (dataset Phase 0) with ≥ 200 utterances, so p95 means something. Keep other apps quiet or record the load average.

## Reproduce

`bias.json` (protobuf JSON of `BiasList`; aliases may be plain strings):

```json
{"terms": [
  {"id": "t1", "canonical": "kubectl", "aliases": ["cube control", "kube c t l"], "weight": 0.9, "kind": "TERM_KIND_COMMAND"},
  {"id": "t2", "canonical": "PyTorch", "aliases": ["pie torch"], "weight": 0.7, "kind": "TERM_KIND_PRODUCT"},
  {"id": "t3", "canonical": "Hyprland", "aliases": ["hyper land"], "weight": 0.6, "kind": "TERM_KIND_PRODUCT"},
  {"id": "t4", "canonical": "gRPC", "aliases": ["g r p c"], "weight": 0.5, "kind": "TERM_KIND_ACRONYM"}
]}
```

```sh
cd server && uv sync --extra mlx
uv run hark-server --backend parakeet_mlx --listen 127.0.0.1:51020 &
../tools/hark-probe/hark-probe /tmp/hark-bench/short.wav --server 127.0.0.1:51020 \
  --repeat 5 --words --bias bias.json --json-summary rt-short.json      # add --fast for back-to-back
```
