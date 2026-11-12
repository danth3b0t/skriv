"""Developer prompts for the synthetic part of eval/dev-v1 (scripts/make-eval-set).

Each prompt is what a developer says to a coding agent:

- ``said``: verbatim, fillers included, as a transcriber would write the speech (spoken symbols
  as words: "dash dash dry run"; tool and product names in their usual spelling).
- ``tts``: what the synthesizer reads when ``said`` would be mispronounced (``pnpm`` -> "p n p m").
  Neither ever contains the written form of a critical string that is spoken as words.
- ``critical``: exact strings a good system writes (identifiers, paths, flags, numbers).
- ``terms``: the shortlist entries the context would plausibly hold for this prompt (the needed
  identifiers, as the agent session / terminal / repo would show them).
- ``forbidden``: false substitutions (an ordinary word turned into a context term) and words a
  self-correction retracted.
- ``app``: term (Ghostty, Alacritty, kitty), code (VS Code chat), slack, notes (Obsidian).
- ``project``: whose repository the shortlist distractors come from.
- ``ctx``: full (needed terms + 15-28 distractors), sparse (needed + a few), empty (no terms).
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Prompt:
    said: str
    critical: list[str] = field(default_factory=list[str])
    terms: list[str] = field(default_factory=list[str])
    forbidden: list[str] = field(default_factory=list[str])
    tts: str = ""
    app: str = "term"
    project: str = "hark"
    ctx: str = "full"
    tags: tuple[str, ...] = ()


P = Prompt
PROMPTS: list[Prompt] = [
    # -- short commands -----------------------------------------------------------------------
    P("run the tests again", tags=("short",)),
    P("yes, do it", ctx="sparse", tags=("short",)),
    P("looks good, ship it", ctx="empty", tags=("short",)),
    P("no, stop, revert that", tags=("short", "negation")),
    P("git status", ["git status"], tags=("short", "command")),
    P(
        "um, run pnpm install and then pnpm dev",
        ["pnpm install", "pnpm dev"],
        ["pnpm"],
        tts="um, run p n p m install and then p n p m dev",
        project="web",
        tags=("short", "command", "filler"),
    ),
    P(
        "uv run pytest dash k gate",
        ["uv run pytest -k gate"],
        ["pytest", "uv", "gate.py"],
        tts="u v run pie test dash k gate",
        tags=("short", "command", "flag"),
    ),
    P(
        "commit this with the message fix colon handle empty transcripts",
        ["fix:"],
        tags=("command", "symbols"),
    ),
    P(
        "show me the diff for pipeline dot py",
        ["pipeline.py"],
        ["pipeline.py", "client/src/hark_client/pipeline.py"],
        tags=("short", "path"),
    ),
    P(
        "cube control get pods dash n staging",
        ["kubectl get pods", "-n staging"],
        ["kubectl", "staging"],
        project="ops",
        ctx="empty",
        tags=("short", "command", "flag"),
    ),
    P("continue, please", ctx="empty", tags=("short",)),
    P("never push directly to main", ctx="empty", tags=("short", "negation")),
    # -- identifiers ----------------------------------------------------------------------------
    P(
        "rename use session store to use workspace store everywhere",
        ["useSessionStore", "useWorkspaceStore"],
        ["useSessionStore", "useWorkspaceStore"],
        app="code",
        project="web",
        tags=("identifier",),
    ),
    P(
        "in the use effect hook, um, add a cleanup function that clears the interval",
        ["useEffect"],
        ["useEffect", "clearInterval", "setInterval"],
        app="code",
        project="web",
        tags=("identifier", "filler"),
    ),
    P(
        "the bug is in snapshot json, it drops the provenance when the focus is a terminal",
        ["snapshot_json"],
        ["snapshot_json", "provenance"],
        tags=("identifier",),
    ),
    P(
        "make load snapshot return an empty context snapshot instead of raising",
        ["load_snapshot", "ContextSnapshot"],
        ["load_snapshot", "ContextSnapshot", "retry.py"],
        tags=("identifier",),
    ),
    P(
        "add a field called confident anchor to gate config, default zero point nine seven",
        ["confident_anchor", "GateConfig", "0.97"],
        ["confident_anchor", "GateConfig", "max_distance"],
        tags=("identifier", "number"),
    ),
    P(
        "why does wait final time out when the server sends the result late?",
        ["wait_final"],
        ["wait_final", "TranscribeStream", "FinalResult"],
        tags=("identifier",),
    ),
    P(
        "rename the is terminal flag on focus info to, uh, terminal like",
        ["is_terminal", "FocusInfo"],
        ["is_terminal", "FocusInfo"],
        tags=("identifier", "filler"),
    ),
    P(
        "um, so the dry run injector should, like, record what it would paste, not print it",
        ["DryRunInjector"],
        ["DryRunInjector", "inject.py"],
        tags=("identifier", "filler", "negation"),
    ),
    P(
        "check whether smart spacing is off when check focus is false",
        ["smart_spacing", "check_focus"],
        ["smart_spacing", "check_focus"],
        tags=("identifier",),
    ),
    P(
        "set min speech ms to two hundred and preroll ms to three hundred",
        ["min_speech_ms", "preroll_ms", "200", "300"],
        ["min_speech_ms", "preroll_ms"],
        tts="set min speech M S to two hundred and pre roll M S to three hundred",
        tags=("identifier", "number"),
    ),
    P(
        "the transcribe stream class leaks a thread when cancel is called twice",
        ["TranscribeStream"],
        ["TranscribeStream", "asr.py"],
        tags=("identifier",),
    ),
    P(
        "add a test for competition key with parse config and parse args",
        ["competition_key", "parse_config", "parse_args"],
        ["competition_key", "parse_config", "parse_args", "rank.py"],
        tags=("identifier",),
    ),
    P(
        "is interesting identifier returns true for single letters, that's wrong",
        ["is_interesting_identifier"],
        ["is_interesting_identifier", "aliases.py"],
        app="code",
        tags=("identifier",),
    ),
    P(
        "bump the parakeet mlx package to version zero point seven point one",
        ["parakeet-mlx", "0.7.1"],
        ["parakeet-mlx"],
        tags=("identifier", "number"),
    ),
    P(
        "use the llama server on port eight zero eight zero, not the one on eight zero eight one",
        ["llama-server", "8080", "8081"],
        ["llama-server", "llama-up"],
        tags=("identifier", "number", "negation"),
    ),
    P(
        "hark server should log the boost min and boost max it actually uses",
        ["hark-server", "boost_min", "boost_max"],
        ["hark-server", "boost_min", "boost_max"],
        tags=("identifier",),
    ),
    P(
        "can you add type hints to read wav and write wav? basedpyright complains about the numpy arrays",
        ["read_wav", "write_wav", "basedpyright"],
        ["read_wav", "write_wav", "basedpyright", "audio.py"],
        tts="can you add type hints to read wave and write wave? based pie right complains about the numpy arrays",
        tags=("identifier",),
    ),
    P(
        "explain what retry dot save does with the context dot json file",
        ["retry.save", "context.json"],
        ["retry.save", "context.json", "retry.py"],
        tags=("identifier", "path"),
    ),
    P(
        "add an index on user underscore id in the sessions table",
        ["user_id"],
        ["user_id", "sessions"],
        project="web",
        ctx="empty",
        tags=("identifier",),
    ),
    P(
        "replace requests dot get with httpx dot async client",
        ["requests.get", "httpx.AsyncClient"],
        ["httpx.AsyncClient", "requests.get"],
        tts="replace requests dot get with h t t p x dot async client",
        project="ml",
        tags=("identifier",),
    ),
    P(
        "use use query from tanstack query instead of fetching in a use effect",
        ["useQuery", "useEffect"],
        ["useQuery", "TanStack Query", "useEffect"],
        project="web",
        tags=("identifier",),
    ),
    # -- paths and flags ---------------------------------------------------------------------
    P(
        "open client slash src slash hark underscore client slash pipeline dot py",
        ["client/src/hark_client/pipeline.py"],
        ["client/src/hark_client/pipeline.py"],
        tags=("path",),
    ),
    P(
        "run hark eval run dash dash set eval slash dev dash v one",
        ["--set", "eval/dev-v1"],
        ["eval/dev-v1", "hark eval"],
        tags=("path", "flag", "command"),
    ),
    P(
        "add a dash dash json flag to hark history list",
        ["--json", "hark history"],
        ["hark history", "--json"],
        tags=("flag",),
    ),
    P(
        "run ruff check with dash dash fix and then basedpyright",
        ["--fix", "basedpyright"],
        ["ruff", "basedpyright"],
        tts="run ruff check with dash dash fix and then based pie right",
        tags=("flag", "command"),
    ),
    P(
        "delete the dot venv folder and run uv sync again",
        [".venv", "uv sync"],
        ["uv", ".venv"],
        tts="delete the dot venv folder and run u v sync again",
        ctx="sparse",
        tags=("path", "command"),
    ),
    P(
        "look at docs slash protocol slash ctl dash v one dot md before you change the reply format",
        ["docs/protocol/ctl-v1.md"],
        ["docs/protocol/ctl-v1.md"],
        tts="look at docs slash protocol slash C T L dash v one dot M D before you change the reply format",
        tags=("path",),
    ),
    P(
        "pass dash dash no dash history to hark once",
        ["--no-history", "hark once"],
        ["--no-history", "hark once"],
        tags=("flag",),
    ),
    P(
        "git push dash dash force with lease, but don't force push main",
        ["--force-with-lease"],
        ["--force-with-lease"],
        tags=("flag", "negation"),
    ),
    P(
        "npm install zod at version three point twenty three",
        ["zod", "3.23"],
        ["zod"],
        project="web",
        ctx="empty",
        tags=("number", "command"),
    ),
    P(
        "set the environment variable hark underscore models underscore dir to slash data slash models",
        ["HARK_MODELS_DIR", "/data/models"],
        ["HARK_MODELS_DIR", "fetch-models"],
        tags=("identifier", "path"),
    ),
    P(
        "tail the logs with journalctl dash dash user dash u hark",
        ["journalctl --user -u hark"],
        ["journalctl", "hark.service"],
        tts="tail the logs with journal control dash dash user dash u hark",
        tags=("flag", "command"),
    ),
    P(
        "chmod plus x scripts slash make dash eval dash set",
        ["chmod +x", "scripts/make-eval-set"],
        ["scripts/make-eval-set"],
        tts="chmod plus x scripts slash make dash eval dash set",
        tags=("path", "command"),
    ),
    P(
        "use the kube config from tilde slash dot kube slash config",
        ["~/.kube/config"],
        ["~/.kube/config", "kubectl"],
        tts="use the cube config from tilde slash dot cube slash config",
        project="ops",
        tags=("path",),
    ),
    P(
        "docker compose up dash d, then curl localhost colon eight thousand slash health",
        ["docker compose up -d", "localhost:8000/health"],
        ["docker compose", "localhost:8000"],
        project="ops",
        tags=("command", "flag", "number"),
    ),
    P(
        "switch the branch to feat slash eval dash set and rebase on main",
        ["feat/eval-set"],
        ["feat/eval-set", "main"],
        tags=("path",),
    ),
    P(
        "in tauri dot conf dot json, set the window to not be resizable",
        ["tauri.conf.json"],
        ["tauri.conf.json", "Tauri"],
        tts="in tow ree dot conf dot json, set the window to not be resizable",
        project="web",
        tags=("path", "negation"),
    ),
    P(
        "the error says module not found, can't resolve at slash lib slash utils",
        ["@/lib/utils"],
        ["@/lib/utils", "tsconfig.json"],
        project="web",
        tags=("path", "negation"),
    ),
    # -- numbers and versions ------------------------------------------------------------------
    P(
        "we need python three point twelve, three point thirteen breaks grpc",
        ["3.12", "3.13"],
        ["gRPC", "pyproject.toml"],
        tts="we need python three point twelve, three point thirteen breaks g r p c",
        tags=("number",),
    ),
    P(
        "set final timeout ms to fifteen hundred",
        ["final_timeout_ms", "1500"],
        ["final_timeout_ms", "config.py"],
        tts="set final timeout M S to fifteen hundred",
        tags=("identifier", "number"),
    ),
    P(
        "close issue four twenty one and link it from PR four thirty seven",
        ["421", "437"],
        tts="close issue four twenty one and link it from P R four thirty seven",
        ctx="sparse",
        tags=("number",),
    ),
    P(
        "the model is parakeet tdt zero point six b v three",
        ["parakeet-tdt-0.6b-v3"],
        ["parakeet-tdt-0.6b-v3"],
        tts="the model is parakeet T D T zero point six B V three",
        tags=("identifier", "number"),
    ),
    P(
        "bump the docker image to node twenty two alpine",
        ["node:22-alpine"],
        ["node:22-alpine", "Dockerfile"],
        project="ops",
        tags=("number",),
    ),
    P(
        "pip install torch two point five with cuda twelve point four",
        ["torch", "2.5", "12.4"],
        ["torch", "CUDA"],
        project="ml",
        ctx="empty",
        tags=("number", "command"),
    ),
    # -- negations -----------------------------------------------------------------------------
    P("don't touch the server code, only change the client", ctx="empty", tags=("negation",)),
    P("this should not retry when the user cancels", tags=("negation",)),
    P(
        "never paste into a window that lost focus, copy it to the clipboard instead",
        tags=("negation",),
    ),
    P(
        "I don't think we need the anchor gate for short clips, but don't remove it yet",
        terms=["gate.py", "GateConfig"],
        tags=("negation",),
    ),
    P(
        "the cleanup must not drop negations, ever, and it shouldn't add anything either",
        tags=("negation",),
    ),
    P(
        "no, that's wrong, the gate isn't the problem, the canonicalizer is",
        tags=("negation",),
    ),
    P(
        "it's not Hyprland, it's the quickshell bar that crashes",
        ["Hyprland", "QuickShell"],
        ["Hyprland", "QuickShell", "Omarchy"],
        tts="it's not hyper land, it's the quick shell bar that crashes",
        tags=("negation",),
    ),
    P(
        "explain why use memo doesn't help here, the component still re-renders on every keystroke",
        ["useMemo"],
        ["useMemo", "useCallback"],
        app="code",
        project="web",
        tags=("negation", "identifier"),
    ),
    # -- self-corrections ----------------------------------------------------------------------
    P(
        "use Postgres, no wait, SQLite for the cache",
        ["SQLite"],
        ["SQLite", "Postgres"],
        ["Postgres"],
        tts="use Postgres, no wait, sequel light for the cache",
        tags=("correction",),
    ),
    P(
        "set the timeout to three seconds, sorry, five seconds",
        forbidden=["three seconds", "3 seconds"],
        ctx="empty",
        tags=("correction",),
    ),
    P(
        "put it in client slash src, actually no, put it in server slash src",
        ["server/src"],
        ["server/src", "client/src"],
        ["client/src"],
        tags=("correction", "path"),
    ),
    P(
        "call it fetch rows, I mean fetch row batch",
        ["fetch_row_batch"],
        ["fetch_rows", "fetch_row_batch"],
        ["fetch_rows"],
        project="ml",
        tags=("correction", "identifier"),
    ),
    P(
        "run it on the GPU server, no, on the CPU fallback",
        forbidden=["GPU"],
        tags=("correction",),
    ),
    P(
        "write it in TypeScript, um, no, in Rust",
        ["Rust"],
        forbidden=["TypeScript"],
        ctx="empty",
        tags=("correction", "filler"),
    ),
    P(
        "add it to the readme, scratch that, add it to docs slash eval dot md",
        ["docs/eval.md"],
        ["docs/eval.md", "README.md"],
        ["README"],
        tags=("correction", "path"),
    ),
    P(
        "make the limit ten megabytes, no, twenty megabytes",
        forbidden=["ten", "10"],
        app="code",
        tags=("correction", "number"),
    ),
    # -- long prompts with fillers ------------------------------------------------------------------
    P(
        "okay so, um, here's the thing. when I dictate into Ghostty and the context collect misses "
        "the deadline, the session start goes out with zero bias terms. uh, I want you to log that "
        "as a warning with the app id, and, like, add a counter to hark doctor so we can see how "
        "often it happens. don't change the deadline itself.",
        ["Ghostty", "hark doctor"],
        ["Ghostty", "SessionStart", "app_id", "hark doctor", "deadline_ms"],
        tts="okay so, um, here's the thing. when I dictate into ghosty and the context collect "
        "misses the deadline, the session start goes out with zero bias terms. uh, I want you to "
        "log that as a warning with the app id, and, like, add a counter to hark doctor so we can "
        "see how often it happens. don't change the deadline itself.",
        tags=("long", "filler", "negation"),
    ),
    P(
        "so basically the problem is that the cleanup model sometimes rewrites dash dash dry run "
        "into dry run without the dashes. can you add flags to the protected spans in cleanup dot "
        "py and add a test with, you know, three or four flags, like dash dash force, dash dash "
        "verbose and dash v",
        ["--dry-run", "cleanup.py", "--force", "--verbose"],
        ["--dry-run", "cleanup.py", "protected_spans", "test_client_cleanup.py"],
        tags=("long", "filler", "flag"),
    ),
    P(
        "on Hyprland the paste goes to the wrong window if I switch workspaces while it's "
        "transcribing. um, we should check the window id from hyprctl active window right before "
        "we paste, and if it changed, uh, just copy to the clipboard and notify.",
        ["Hyprland", "hyprctl"],
        ["Hyprland", "hyprctl", "window_id", "inject.py"],
        tts="on hyper land the paste goes to the wrong window if I switch workspaces while it's "
        "transcribing. um, we should check the window id from hyper control active window right "
        "before we paste, and if it changed, uh, just copy to the clipboard and notify.",
        tags=("long", "filler"),
    ),
    P(
        "in the PyTorch training loop, uh, move the optimizer zero grad call before the backward "
        "pass, and don't forget to call model dot train at the start of each epoch",
        ["PyTorch", "zero_grad", "model.train"],
        ["zero_grad", "model.train()", "optimizer", "train.py"],
        tts="in the pie torch training loop, uh, move the optimizer zero grad call before the "
        "backward pass, and don't forget to call model dot train at the start of each epoch",
        project="ml",
        tags=("long", "filler", "negation", "identifier"),
    ),
    P(
        "the Omarchy panel should show, like, the last three dictations, and clicking one should "
        "run hark ctl paste last",
        ["Omarchy", "hark ctl paste-last"],
        ["Omarchy", "paste-last", "hark ctl", "hark-panel"],
        tts="the oh marky panel should show, like, the last three dictations, and clicking one "
        "should run hark control paste last",
        tags=("long", "filler", "command"),
    ),
    P(
        "add a Ghostty keybind for super alt d that runs hark ctl toggle",
        ["Ghostty", "hark ctl toggle"],
        ["Ghostty", "hark ctl"],
        tts="add a ghosty keybind for super alt D that runs hark control toggle",
        tags=("command",),
    ),
    P(
        "the ctl socket should live in dollar XDG runtime dir, not in slash tmp",
        ["$XDG_RUNTIME_DIR", "/tmp"],  # noqa: S108 - an expected text
        ["$XDG_RUNTIME_DIR", "ctl.py", "socket_path"],
        tts="the C T L socket should live in dollar X D G runtime dir, not in slash temp",
        tags=("negation", "identifier", "path"),
    ),
    P(
        "pnpm add dash capital d vitest, then create a vitest dot config dot ts with the jsdom environment",
        ["pnpm add -D vitest", "vitest.config.ts"],
        ["vitest", "vitest.config.ts", "jsdom", "pnpm"],
        tts="p n p m add dash capital D vee test, then create a vee test dot config dot T S with "
        "the J S dom environment",
        project="web",
        tags=("long", "command", "flag"),
    ),
    P(
        "in the redis config set max memory policy to all keys LRU and don't enable persistence",
        ["maxmemory-policy", "allkeys-lru"],
        ["maxmemory-policy", "allkeys-lru", "redis.conf"],
        tts="in the redis config set max memory policy to all keys L R U and don't enable persistence",
        project="ops",
        tags=("identifier", "negation"),
    ),
    P(
        "set CUDA visible devices to zero comma one before you start the training run",
        ["CUDA_VISIBLE_DEVICES"],
        ["CUDA_VISIBLE_DEVICES", "torchrun"],
        project="ml",
        tags=("identifier",),
    ),
    P(
        "okay, uh, so the eval set should have, you know, about a hundred clips, mostly terminal "
        "prompts, and it should never include anything from my real history without asking first",
        tags=("long", "filler", "negation"),
    ),
    P(
        "I think the canonicalizer is too eager. when the shortlist has wait for and the user says "
        "wait for the build, we must not write it with an underscore",
        ["wait for the build"],
        ["wait_for", "canon.py"],
        ["wait_for the"],
        tags=("long", "negation", "trap"),
    ),
    # -- prose apps ----------------------------------------------------------------------------------
    P(
        "Refactor the router so the health probe for each provider runs in parallel. Keep max "
        "latency ms as the per provider budget.",
        ["max_latency_ms"],
        ["max_latency_ms", "Router", "providers.py"],
        tts="Refactor the router so the health probe for each provider runs in parallel. Keep max "
        "latency M S as the per provider budget.",
        app="code",
        tags=("identifier",),
    ),
    P(
        "hey, I pushed the fix for the paste bug, um, can you test it on your mac before we merge? "
        "I don't have one here.",
        app="slack",
        tags=("negation", "filler"),
    ),
    P(
        "meeting notes: we decided to keep S1 mini as the default cleanup model and not switch to "
        "Qwen three for now",
        ["S1-mini", "Qwen3"],
        ["S1-mini", "Qwen3", "llama-server"],
        tts="meeting notes: we decided to keep S one mini as the default cleanup model and not "
        "switch to kwen three for now",
        app="notes",
        tags=("negation",),
    ),
    P(
        "write a short summary of what changed on this branch for the PR description, keep it "
        "under five bullet points",
        app="code",
        ctx="sparse",
        tags=("prose",),
    ),
    P(
        "the build is rough around the edges, but it works, so let's merge it",
        terms=["ruff"],
        forbidden=["ruff"],
        app="slack",
        tags=("trap",),
    ),
    P(
        "Highland Park is where the offsite is, so don't book anything downtown",
        terms=["Hyprland"],
        forbidden=["Hyprland"],
        app="slack",
        tags=("trap", "negation"),
    ),
    # -- traps in the terminal (ordinary words that look like context terms) -------------------------
    P(
        "write a quick shell script that waits for the server to come up",
        terms=["QuickShell", "wait_for"],
        forbidden=["QuickShell", "wait_for"],
        tags=("trap",),
    ),
    P(
        "show me the last text you generated, and keep it to one line",
        terms=["last_text", "one_line"],
        forbidden=["last_text", "one_line"],
        tags=("trap",),
    ),
    P(
        "we need a session start and end time in the report",
        terms=["SessionStart"],
        forbidden=["SessionStart"],
        tags=("trap",),
    ),
    P(
        "the hard part is the timing, not the model",
        terms=["hark"],
        forbidden=["hark part"],
        tags=("trap", "negation"),
    ),
    P(
        "mark down the time when the user cancels, so we can compare it later",
        terms=["mark_down"],
        forbidden=["mark_down"],
        tags=("trap",),
    ),
    P(
        "it should read the text field first and only then fall back to the screen text",
        terms=["TextField", "ScreenText"],
        forbidden=["TextField", "ScreenText"],
        tags=("trap",),
    ),
]
