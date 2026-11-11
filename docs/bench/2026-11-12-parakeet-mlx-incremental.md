# Bench: incremental (sentence-segment) decoding in the parakeet_mlx backend (2026-11-12)

> **TTS caveat.** Same as the [boost bench](2026-11-11-parakeet-mlx-boost.md): macOS `say`, two voices, clean and
> evenly paced. Good for comparing incremental against offline on the same audio, not for absolute accuracy claims.

## Result in one paragraph

Long dictations used to be decoded only at end of audio. On the 87 s real dictation in the stress history, the server
spent 2.4 s from end of audio to the final result, 2.1 s of it in decoding. The backend now decodes utterances of
30 s and more while they stream, a sentence at a time, so end of audio only decodes the last few seconds. On ten
39–120 s TTS dictations with the 39-term shortlist, the end-of-audio cost drops from p50 2044 ms (max 4982 ms) to
**p50 249 ms (max 424 ms)**. WER changes from 0.063 to 0.065 (greedy: 0.066 → 0.069). In real time through the
server and the client, key-up→final for a 60 s dictation drops from 2.18 s to **0.28 s**, and for 120 s from up to
4.27 s to **0.25–0.27 s**. Below 30 s nothing changes, because the WER cost there was +1.5 points on one condition
of the short clip set (see below).

## Design

| | |
|---|---|
| Segments | A segment ends at a **sentence end**: the widest pause between a sentence-final token (`.`, `?`, `!`) and the next word. Each segment is decoded like an utterance of its own: fresh decoder state, greedy, boosted beam 4, and the deletion guard (unchanged from the boost bench). |
| Encoder | Full attention over `[cut − 16 s, window end]`, so the encoder sees left context, and right context past every cut (≥ 2 s). The encoder is cheap (~30–90 ms per window). |
| Window | Tried once 6 s + 2 s of audio follow the last cut. A greedy pass from the cut over the whole window finds the sentence ends; the cut must be ≥ 1 s after the previous one and ≥ 2 s before the window end. If there is no sentence end, the window is retried with 3 s more audio. Past 30 s of uncut audio, it stops trying and end of audio decodes the rest. |
| Greedy reuse | Greedy tokens up to the cut come from that look-ahead pass. Only the beam runs again, on the segment frames. |
| Tail | At end of audio, any running window is awaited, then `[last cut, end]` is decoded the same way (typically 3–6 s of audio). The anchor (greedy) is assembled the same way and stays free. |
| Scheduling | One window at a time per session, on the backend's worker thread, interleaved with other sessions' decodes. Nothing runs before the audio reaches `incremental_min_ms` (30 s), so short dictations never queue behind a window. A `ContextUpdate` restarts the segments with the new bias. |
| Knobs | `[backend.options] incremental_min_ms` (30000; −1 turns it off) and `incremental_stride_ms` (6000). Capabilities report `STREAMING_MODE_INCREMENTAL`. Timings: `backend_ms.windows`, `windows_discarded` (deletion guard), `tail_audio` (ms decoded at end of audio). |

### What did not work

1. **Fixed-stride cuts with the decoder restarted a little before the cut** (2 s warm-up, words assigned by start
   time): WER 0.066 → 0.185. A fresh TDT decoder that starts **inside a sentence emits nothing until the next sentence
   starts**. For example, a decode starting at 19.92 s produced its first word at 24.40 s, while starting at 19.5 s, in a
   pause, was fine. Whole clauses were lost at every window.
2. **Carrying the decoder state across windows** (last token, LSTM state, booster state; cuts in word gaps): WER
   0.066 → 0.104, and still dropped whole sentences in 2 of 10 clips. Whether the decoder picks up the next sentence
   after a `.` turned out to be a tipping point: with identical carried state, encoder windows starting at 0/2/4/10/12 s
   caught "The part that still bothers me…", but windows starting at 6 or 8 s did not, and the decoder stayed silent
   until the following sentence. Offline decoding just happens to land on the right side of it.
3. **Word gaps from TDT token spans**: a token's duration runs to the next emission, so token spans are contiguous and
   have no gaps. Only token **starts** time the words. The cut sits halfway between the `.` token's start and the next
   word's start.

Starting a fresh decoder at a sentence boundary is what the model was trained on (every training utterance starts
that way). Cutting only there removed the dropped sentences.

## Accuracy and end-of-audio cost (in process, `run_bench.py accuracy`)

Each clip is decoded offline and incrementally with the same BiasPlan. The incremental driver feeds 40 ms chunks and
waits for each window it starts, the way a worker that keeps up with real time would. "end of audio" is the offline
decode against `IncrementalStream.finish`. Windows ran for every clip (`--min-ms 0`). Host: MacBook M5 Pro, the
user's own hark-server idle in parallel.

Long clips ([`long.json`](parakeet-mlx-incremental/long.json): 4 texts × Samantha/Daniel at 150 wpm, 39–63 s, plus
one 114–120 s concatenation):

| condition | WER offline | WER incremental | incremental vs offline (WER between the two texts) | end of audio: offline p50 / max | incremental p50 / max | window work per audio s |
|---|---|---|---|---|---|---|
| none (greedy) | 0.066 | 0.069 | 0.026 | 457 / 1067 ms | 96 / 135 ms | 33 ms |
| shortlist (beam 4 + boost) | 0.063 | 0.065 | 0.020 | 2044 / 4982 ms | 249 / 424 ms | 60 ms |

Short clips (the boost bench's 20 clips, 2–21 s; only the 9–21 s ones get a window):

| condition | WER offline | WER incremental | incremental vs offline | end of audio: offline p50 / max | incremental p50 / max |
|---|---|---|---|---|---|
| none | 0.393 | 0.408 | 0.031 | 62 / 226 ms | 61 / 165 ms |
| shortlist | 0.382 | 0.385 | 0.004 | 194 / 1200 ms | 193 / 815 ms |

The +1.5 points (none, short clips) come from 3 clips and are spelling-level noise: "parsecli args" vs "parse cli args",
"Hipperland" vs "hipper land", and segment-initial capitals ("Rename" after a cut where offline wrote ", rename").
That is still more than the 1 point the task allowed, so **utterances under 30 s stay offline**. Under 30 s the
offline finalize costs ≤ ~1.2 s with boosting (20 s: ~0.9 s). On long clips the loss is 0.2–0.3 points.

Left-context sweep (greedy, long clips, an earlier prototype with the same segmentation): left 0 / 4 / 8 / 16 / 24 s
→ WER 0.073 / 0.079 / 0.073 / **0.069** / 0.071 (offline 0.066). Right context 3 s instead of 2 s: 0.071 at left 8 s,
no change at left 16 s.

## End to end, real time (client → server, key-up→final)

`hark once`-style probe (the client pipeline, 40 ms chunks paced in real time, repo context with 60 bias terms,
cleanup off). Before: the same server with `incremental_min_ms = -1`. 3 runs each.

| clip | audio | before p50 (max) | after p50 (max) | after: windows / tail decoded at end |
|---|---|---|---|---|
| twenty-Samantha | 19.9 s | 915 ms (1025) | 888 ms (922), offline as before | — |
| commands-Daniel | 40.1 s | — | 276 ms (277) | 9 / 5.6 s |
| review-Samantha | 60.4 s | 2181 ms (2213) | **282 ms (285)** | 7 / 5.8 s |
| review+feedback-Daniel | 119.6 s | 2627 ms (4266)¹ | **254 ms (271)** | 17 / 5.0 s |

¹ With the client's default `final_timeout_ms = 5000`, the first try of this clip timed out. The probe raised it to 20 s.

EndOfAudio still reaches the server within 1 ms of leaving the client while windows run. The window work (≤ 60 ms per
audio second with boosting) does not starve the server's event loop.

## Limitations

- TTS only. Real dictation has pauses (more sentence ends, easier cuts) but also fillers and run-on sentences. A
  sentence longer than 30 s is decoded at end of audio, as before.
- The segment-initial capital can differ from offline (a new segment starts like an utterance).
- One window at a time per session on the shared worker. Two long dictations at once would queue behind each other's
  windows.
- The 30 s threshold is set on TTS; real recordings (dataset phase 0) should decide whether it can go lower.

## Reproduce

```sh
uv sync
B=docs/bench/parakeet-mlx-incremental
uv run python $B/run_bench.py make-clips /tmp/hark-long                      # say + afconvert, 10 WAVs
uv run python $B/run_bench.py accuracy /tmp/hark-long                        # long-clip table (+ per-clip text on stderr)
uv run python docs/bench/parakeet-mlx-boost/run_bench.py make-clips /tmp/hark-bias
uv run python $B/run_bench.py accuracy /tmp/hark-long --short /tmp/hark-bias --only-short
```
