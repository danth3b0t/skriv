# hark — project context (working name)

Context-aware, local-first dictation tool. Linux-first (daily driver: Linux laptop, no GPU, Hyprland/Omarchy), macOS second.
Behaves like Superwhisper: hotkey → speak → text pasted into the focused input. No mandatory review; no coding-agent extensions required.

## Architecture decisions so far
- **Client/server split.** Thin Rust client on each machine: hotkey, audio capture, Silero VAD, passive context gathering
  (focused app/window, AX/AT-SPI focused text + selection, terminal text, terminal cwd → git repo vocabulary, own history/aliases;
  opt-in: agent session files read from disk, OCR), context ranker → shortlist (~20–100 terms with spoken aliases),
  canonicalization, guardrails, paste, undo/swap-to-raw, history.
- **Inference servers** the user owns: desktop Linux RTX 3060 12 GB (daily), Mac M5 Pro 48 GB (dev/backup),
  university H100s (evaluation/training only, intermittent). Laptop needs a local CPU fallback when offline.
  Servers may be Python (NeMo GPU-PB Parakeet-TDT, Qwen3-ASR with free-text context), NeMo-Speech.cpp (Riva-compatible gRPC
  with per-request `speech_contexts`), FluidAudio/MLX on Mac, llama.cpp/vLLM/SGLang for cleanup (OpenAI-compatible, JSON schema).
- **Client streams audio while the user speaks**; after key-up only finalize. Target ≤1 s p95 key-up → pasted (LAN).
- **Server must return** final text + N-best/alternatives + word timings + confidences so the client can do acoustic gating,
  canonicalization and fusion regardless of backend.
- **Provider registry** in client config: capabilities per provider (per-request bias list, free-text context, streaming,
  N-best, word timestamps), routing/fallback chain with health checks, per-provider privacy policy (may context be sent?).
  Also adapters to third-party APIs (OpenAI-compatible transcription/chat, Riva gRPC, Deepgram keyterms, Voxtral context_bias).
- Cleanup: optional small model emitting a constrained **edit list** (find/replace/reason) validated by the client;
  protected spans (identifiers, numbers, negations) must not change; deadline → fall back to raw text.
- Security: Tailscale/WireGuard network, mTLS and/or bearer tokens; context is sensitive (screen text, code).

## Research findings to respect
- Inject context INTO ASR (decode-time boosting) rather than relying on post-hoc LLM correction; text-only LLM correction often damages correct transcripts.
- Short, relevance-filtered lists beat large dumps; always keep an unbiased/no-context anchor; measure false substitutions with distractor context.
- Qwen3-ASR context = system-prompt text; can echo context. GPU-PB per-stream boosting works with greedy label-looping TDT.
- Evaluation: no public dataset pairs real dictation with desktop/terminal/repo context. Closest: ScreenASR-Bench (arXiv 2610.16260),
  SlideSpeech, ContextASR-Bench, HearInContext, VoiceCodeBench. We will build our own dataset from real recordings.
