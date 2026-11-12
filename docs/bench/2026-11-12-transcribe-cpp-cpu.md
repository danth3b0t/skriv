# Bench: transcribe.cpp Parakeet on CPU and Metal, the offline fallback (2026-11-12)

> **TTS and host caveats.** macOS `say` (Samantha, Daniel), clean and evenly paced: fine for latency and for comparing
> backends on the same audio, not for absolute accuracy. Host: MacBook M5 Pro (6 P + 12 E cores, 48 GB). The machine
> was **busy** (load average 4–5 in run 1, 7–8 in run 2: the user's own hark-server, llama-server and other work), and
> macOS cannot pin threads to P-cores, so CPU numbers with few threads are noisy (see the max column). Thread counts are
> only a rough stand-in for the laptop; run the bench there for real numbers (clips can be copied over).

## Result in one paragraph

The `transcribe_cpp` backend runs Parakeet TDT 0.6B v3 **Q4_K_M** (485 MB GGUF) on transcribe.cpp 0.4.2 / ggml in a
worker child. On CPU, decode time is linear in audio length and **~95 % encoder**: with 4 threads, 3 s of speech is
decoded in ~0.16–0.25 s, 10 s in ~0.5–1.0 s, 20 s in ~1–2.3 s, 60 s in ~3.2–4.2 s (RTFx 10–20); with all 18 cores
3 s → 89 ms and 60 s → 1.9 s. On Metal, 3 s → 34–88 ms and 60 s → 0.38–0.63 s, close to the parakeet-mlx greedy
decode (60 s → 0.5 s). **Q4_K_M costs no accuracy here**: WER 0.037 at 60 s against 0.035 for parakeet-mlx bf16
greedy, identical on 3/10 s, one extra error in 232 words at 20 s. The worker loads in 0.2–0.3 s (weights are
memory-mapped) and warms up in < 1 s; it holds ~1 GB resident on CPU (463 MB of it mapped weights), 244 MB on Metal.
Through the client with the desktop provider blackholed, `hark once` on the CPU fallback (4 threads) took key-up→final
136 / 445 / 887 / 2839 ms for 3 / 10 / 19 / 58 s, and routing itself cost 0 ms (cached health).

## Setup

| | |
|---|---|
| Build | `scripts/fetch-transcribe` (tag v0.4.2, commit 275cd50; cmake via `uvx` because none is installed; Metal build, which also has the CPU backend; `-DCMAKE_BUILD_TYPE=Release`, shared lib, Accelerate BLAS on macOS) |
| Model | `handy-computer/parakeet-tdt-0.6b-v3-gguf` @ d8d8c32, `parakeet-tdt-0.6b-v3-Q4_K_M.gguf` (sha256 b68557be…) |
| Path measured | `TranscribeCppBackend.decode()` in process: base64 PCM over the worker's stdin, `Session.run(timestamps="token")`, JSON reply. IPC adds 2–5 ms. Warmup as the server does it (1 s + 10 s of noise). |
| Clips | [`run_bench.py`](transcribe-cpp-cpu/run_bench.py) `make-clips`: the first 9/29/58/180 words of the `review` and `cluster` texts of the [incremental bench](parakeet-mlx-incremental/long.json) (identifier-heavy developer speech), `say -r 150`, 2 voices → 2.6–3.2 s, 9.7–10.6 s, 19.1–20.4 s, 56–60.5 s |
| Measure | 5 decodes per clip, median per clip; table: median (and max) over the 4 clips of a length. WER against the spoken text, lower-cased, edge punctuation stripped. Raw rows: [`results.json`](transcribe-cpp-cpu/results.json) |
| Reference | `parakeet_mlx` backend, greedy (no bias plan), bf16, Metal, incremental decoding off |

## Latency (ms, median of clip medians; max in parentheses)

| config (device:threads) | run | 3 s | 10 s | 20 s | 60 s | RTFx | encoder share |
|---|---|---|---|---|---|---|---|
| cpu:18 (default: all cores) | 1 | 89 (92) | 292 (320) | 594 (627) | 1910 (2399) | 31–35 | 93 % |
| cpu:8 | 2 | 242 (478) | 782 (1425) | 682 (727) | 2246 (2287) | 18–29 | 93 % |
| cpu:4 | 2 | 156 (167) | 498 (545) | 982 (1043) | 3222 (3325) | 18–21 | 95 % |
| cpu:4 | 1 | 250 (335) | 990 (1335) | 2320 (4012) | 4190 (5050) | 10–14 | 95 % |
| cpu:2 | 1 | 246 (266) | 845 (869) | 1668 (3609) | 5408 (5707) | 11–12 | 96 % |
| metal | 1 | 56 (88) | 116 (179) | 162 (250) | 566 (627) | 62–143 | 69–79 % |
| parakeet-mlx greedy (reference) | 2 | 55 (58) | 108 (119) | 180 (215) | 517 (543) | 53–113 | 26–58 % |

cpu:8 slower than cpu:4 on the short clips and cpu:4 twice as slow in run 1 as in run 2 are contention, not
scaling: with 4–8 threads some land on E-cores or wait for the other load. Read the CPU rows as a band.

## Accuracy (WER, 4 clips per length)

| | 3 s (36 words) | 10 s (116) | 20 s (232) | 60 s (720) |
|---|---|---|---|---|
| transcribe.cpp Q4_K_M, every config | 0.000 | 0.052 | 0.056 | 0.037 |
| parakeet-mlx bf16 greedy | 0.000 | 0.052 | 0.052 | 0.035 |

All CPU and Metal configurations produce the same text. The errors are the usual unbiased ones on identifiers
("use session store", "Qbectl getPods" for "kubectl get pods"): transcribe.cpp 0.4 has **no vocabulary or hotword input
for Parakeet** (`docs/prompting.md` lists none; `model.supports("vocabulary")` is false), so the backend advertises
bias `NONE`. The client still canonicalizes against its shortlist and runs the anchor gate (no-op here: anchor ==
primary); the acoustic vocabulary verifier (ADR-002 #1) is what would close this gap.

## Memory and start-up

| | load | warmup | worker RSS after the bench |
|---|---|---|---|
| CPU | 0.2–0.3 s | 0.4–0.9 s | 970–1070 MB (463 MB mapped weights, reclaimable page cache) |
| Metal | 0.17 s | 0.7 s | 244 MB |

## What this means for the laptop

- BUFFERED decoding makes key-up→final grow with the dictation: on a laptop CPU 2–4× slower per thread than these
  cores, expect roughly 0.3–0.8 s for a 10 s dictation and several seconds for 60 s. Short dictations are fine;
  long ones are where the parakeet-mlx backend's incremental decoding (sentence segments decoded while the audio
  streams) would help most, and transcribe.cpp's per-call API allows the same approach: next step.
- Install `libopenblas-dev` (or another CBLAS) before `scripts/fetch-transcribe` on Linux: transcribe.cpp uses it for
  the host-side TDT decoder (10–15× faster than its scalar fallback per its README); the decoder is 4–7 % of the time
  here with Accelerate.
- `scripts/fetch-transcribe vulkan` on a laptop with a usable iGPU may cut the encoder share the way Metal does here;
  untested.

## Reproduce

```sh
scripts/fetch-transcribe                    # build + model (macOS: CPU + Metal)
uv run python docs/bench/transcribe-cpp-cpu/run_bench.py make-clips /tmp/tc-clips      # macOS `say`
uv run python docs/bench/transcribe-cpp-cpu/run_bench.py run /tmp/tc-clips \
    --config cpu:0 --config cpu:4 --config cpu:2 --config metal:0 --mlx --json out.json
```
