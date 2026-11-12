# Bench: server robustness (branch server-robust, 2026-11-13)

Work list: failure class 8 of [the baseline](2026-11-13-eval-baseline.md) (degenerate decode of syn-005), a
client guardrail against degenerate recognizer output, and the lost-first-word cases of class 4 (syn-016,
real-011, real-012).

## Class 8: `<unk><unk>…` / `ÌÌÌ…` — fixed (server)

**Root cause.** parakeet-mlx 0.7.0's mel front end (`parakeet_mlx.audio.stft`) cuts frames out of the
reflect-padded audio with `mx.as_strided`, and its last frame reaches up to `n_fft - win_length` = 112 samples
past the end of the buffer. Those samples meet the zero padding of the 400-sample Hann window, so finite garbage
is harmless. But MLX recycles buffers, and when the bytes past the end hold a NaN or an inf left by an earlier
array, `NaN * 0` stays NaN. The per-feature mean and std in `get_logmel` then spread it to every frame, the
encoder output is NaN, greedy decodes `<unk>` at each step and the boosted beam writes `Ì`. Whether it happens
depends on what the allocator handed out before, which is why a particular sequence of sessions triggered it.
syn-005 has 36 700 samples, and its last frame reads 100 samples past the buffer.

**Evidence.** With the real v3 model, filling the allocator with NaN buffers a little larger than syn-005's
padded audio and then decoding syn-005 gives `<unk><unk><unk>…` with the library STFT and `Get status.` with the
fix (probe; not committed). The fixed STFT gives the same mel as the library's, bit for bit, on clean memory
(5 lengths checked, max abs diff 0.0). So every decode that was not hit by the bug is unchanged.

**Reproduction against a fresh server.** The six-clip sequence from the baseline (real-024, syn-001…005) did
*not* reproduce on a freshly started server from this worktree before the fix: four runs of the six clips and
two full runs all gave `Get status.`. The shared :50051 server no longer reproduces it either. That fits the
cause: the trigger is the allocator's contents, which depend on everything the process has run before, not
only on the last six sessions. The deterministic reproduction is therefore at backend level (allocator
poisoned with NaN), and that is what the regression test does.

**Fix.** `stft_in_bounds` in `backends/parakeet_mlx.py` is the library's STFT for the call `get_logmel` makes,
except that it zero-extends the padded audio to cover the last frame. `_load_sync` installs it in
`parakeet_mlx.audio`, like the existing `install_fast_tabulate`. It covers offline, incremental-window and tail
decodes, and both models.

**Test.** `test_mel_never_reads_freed_memory` (server/tests/test_parakeet_mlx.py, fast suite, no weights) fills
the allocator with NaN buffers, decodes through the backend with the real mel front end, and checks that the
mel the encoder receives is finite. It fails on both parameterized lengths without the fix.

## Client guardrail: degenerate output is a failed transcription

`guardrails.degenerate(text)` gives a reason when a recognizer output is mostly `<unk>` / `<|…|>` special tokens
(at least half the non-space characters), or when the whole text, with whitespace removed, is a 1-4 character
unit repeated at least 12 times, optionally followed by one final punctuation mark. Right after recognition, the
pipeline then sets status `error` with `asr: degenerate output (…)`, plays the error sound and notifies with the
`hark retry` hint. Nothing is pasted or copied, the pill shows Failed, and the retry ring keeps the audio
(meta status `error`). The thresholds keep "no no no no", "ffffffff", one `<unk>` inside real text and 11
repeats. Tests: a unit table in `test_client_guardrails.py`, and a daemon-level test in
`test_client_retry.py` (mock server scripted to `<unk>` and to `Ì`).

## Lost first words (class 4): not ours; acoustic

Checked with the real models on the raw clip audio (probes, not committed):

- **The audio reaches the server whole.** `hark eval run` feeds the full WAV with no pre-roll and no mask
  (`audio_ms` matches the file length). In live use, the start cue zeroes `cue_mask_ms` = 250 ms after the
  200 ms pre-roll. Zeroing 200-450 ms of syn-001/002/004/016/020/030 did not change their words: syn-016
  "Eckload" became "Ecload". That is still a live-only risk for someone who speaks right at key-down. It is not
  the cause here, and it is outside this branch's files (daemon/config).
- **syn-016** ("make load snapshot…"): 400 ms of cafeteria noise at about 55 dB precede the speech. v3 greedy
  hears "Eckload", the biased beam "I load", and v2 (English) "Icload". The result is the same with 0.5 s of
  zeros or of the clip's own lead noise prepended, and on the first 4 s alone. The /m/ onset is lost
  acoustically (Kokoro at 1.16×, SNR 10 dB). Neither the start of the decoder nor our front end is involved.
- **real-011** ("who loved each other no…"): energy at 0.2-1.5 s, but the first word comes at 1.6 s. Decoded
  alone, 0-1.7 s gives "Hold job." (v3) or "Oh jar." (v2), and nothing when 1 s of silence follows (v3). Gain ×2
  does not help. Both models hear no "who loved each other" in that audio. It is probably cut speech or
  crosstalk at the EdACC segment boundary.
- **real-012** ("you know the last time I heard…" → "We had a guy sing"): the first word is decoded at 160 ms.
  This is a mishearing of fast, accented speech (both models agree), not a lost start.

No change was made for these.

## Eval (baseline config, own server :50061)

| run | failed | unreviewed | accepted | recognition WER | critical recall (final) | false subs | negations lost |
|---|---|---|---|---|---|---|---|
| baseline report (shared server) | 43 | 28 | 51 | 14.8% | 64% | 12/29 | 2 |
| before (this worktree, fresh server) | 43 | 28 | 51 | 14.4% | 64% | 12/29 | 2 |
| after (fix + guardrail) | 43 | 28 | 51 | 14.4% | 64% | 12/29 | 2 |

Before and after are the same. Neither run hit the allocator trigger, so syn-005 is "Get status." in both,
which is the WER difference from the baseline report (class 4 `git` → "Get" remains). The only text that
differs between before and after is syn-074 ("dash-bores … dash v" vs "dash-bos … dash d"), and it differs
only at cleanup: the shared S1 model is nondeterministic, and the raw text is identical. No regressions.
Latency is higher than in the baseline report in both runs because other agents were using the machine.

No new final texts to review: no clip's text changed.
