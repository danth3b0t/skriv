# Context capture: macOS experiments and Linux research

**Measured 2026-11-11 (UTC), macOS 26.5.1, arm64. Linux is research only.**

## Bottom line

- **Implement bounded focused-text AX reads and a tmux current-pane provider before OCR.** Existing hark source types and rank weights already anticipate `FOCUSED_TEXT` and `TERMINAL_TEXT`; `engine.py` does not populate them.
- **Ghostty 1.4.0 exposes terminal text through AX**, confirming the read-only accessibility capability announced for 1.2. However, its “visible range” is **not a trustworthy viewport boundary**: versioned source returns the whole cached screen buffer, and upstream documents scrollback leakage. Treat this as buffer context, not verified visible text.
- **Terminal.app exposes both text and a range-based read.** The small probe window worked; this was not a large-scrollback test.
- **Helium is only partially demonstrated:** browser chrome text was readable, but the sampled tree had no `AXWebArea`, and app-local focused-element lookup returned no value. Do not claim page/editor capture works.
- **One-window ScreenCaptureKit + Vision works from this Ghostty-hosted process**, despite AX being denied there. Fast OCR took about 31 ms warm-ish median; accurate about 213 ms, plus a one-time 74 ms capture. OCR remains an opt-in fallback, not a prerequisite.
- **tmux 3.8 current-grid capture worked in ~4 ms including process startup**, yielding 11 and 27 unique terms from two attached-client panes. kitty and WezTerm were not available in PATH or the checked `/Applications` bundles.

## Scope, safety and methodology

Read `README.md`, `docs/00-context.md`, and all `client/src/hark_client/context/*.py`. No production code/config changes, no service management, no clipboard access, no synthetic input, no activation/focus calls, and no accessibility-enabling writes. The only AX setter was **client-side `AXUIElementSetMessagingTimeout(..., 0.1)`**.

Used **three** authorized `Terminal do script` launches. The first two stopped on a PyObjC CFRange representation bug (tuple, not `.location`/`.length`); the third produced the complete results. Opening the helper window can itself affect the desktop; nothing attempted to restore or change focus. Apps were enumerated with `NSWorkspace.runningApplications()` (NSRunningApplication objects), then queried by PID, never focused. “Focused” below means **that application's reported focus**, not proof it currently owns global keyboard focus.

Running targets: Ghostty 1.4.0, Terminal 2.15, Helium 0.21.2.1. Safari, Slack, Discord, VS Code/Code and Notes were **not running**, so were neither launched nor tested.

- AX: 20 successive calls per attribute, timeout 100 ms on app and visited elements; stop repetitions on `kAXErrorCannotComplete`. A bounded traversal of one app-local window searched text candidates: at most 160 nodes, depth 12, eight text candidates; checks a five-second traversal budget between nodes. This is **not a hard end-to-end deadline** for all repeated detail calls.
- Reported AX timings cover the call and PyObjC conversion, **not** term extraction/import/startup. Nearest-rank p50/p95; with 20 samples, p95 excludes the largest sample. Cold first/max measurements remain in JSON. No timeout was observed in the successful run; timeout behavior against a deliberately hung app was not tested.
- OCR: one successful capture of one on-screen Ghostty window, selected without activation, held only in memory. Five fast requests then five accurate requests on the **same image**, language correction off. These are repeated-image microbenchmarks, not independent-window p95 estimates. The window need not be the AX-sampled Ghostty window.
- tmux: 20 `capture-pane -p -t <pane>` calls per attached-client target, no `-S -`, history capture, buffer creation, or pane selection. Timing includes subprocess startup. Target panes were not mapped back to the OS frontmost window in this experiment.
- Raw AX/OCR/tmux text stayed in process memory or an anonymous pipe. Artifacts contain **no text, titles, URLs, identifiers, pane/socket names, or screenshots**, only safe labels and measurements. OCR Swift must be piped to the summarizer, not run with stdout saved directly.
- Term yield uses the repo's **actual `extract_terms(text)`**, default `prose=False`, locally. Counts are unique within one sample, not total occurrences or the final ranked shortlist. `extract_terms` emits identifiers, paths and flags but **never `TermKind.NAME`**: all name counts here are zero by construction, not proof that the screens contained no names. Ordinary capitalized names are generally missed. Yield is not relevance or recognition accuracy.

## macOS capability matrix

Times in **milliseconds, p50 / p95**, unless marked single measurement. `I/P` = identifier/path counts. Results are point-in-time and content-dependent.

| Source × app | Capability / sampled content | Latency | Term yield | Permission / prerequisite |
|---|---|---:|---:|---|
| AX, direct Ghostty-hosted Python → all three apps | Denied; focused lookup error `-25211` | Ghostty 0.011/0.013; Helium 0.013/0.019; Terminal 0.038/0.046 | Unavailable, not zero | This process lacks Accessibility |
| AX `AXValue` → Ghostty | `AXTextArea`; 3,156 chars, 53 lines; buffer, not assured viewport | 0.032 / 0.041 | 7 (6 I, 1 P) | Terminal-hosted probe trusted |
| AX range-string → Ghostty | Reported range `(0,3156)`; returned 3,155 chars, 53 lines | 0.035 / 0.046 | 7 (6 I, 1 P) | Same; range correctness caveat |
| AX title → Ghostty | 13 chars | 0.016 / 0.026 | 1 I | Accessibility |
| AX `AXValue` → Terminal | `AXTextArea`; 458 chars, 56 lines | 0.021 / 0.032 | 7 (5 I, 2 P) | Trusted helper; **its own newly opened probe window** |
| AX range-string → Terminal | Range `(0,458)`; 458 chars | 0.025 / 0.028 | 7 (5 I, 2 P) | Same; small synthetic-by-bootstrap sample |
| AX title → Terminal | 31 chars | 0.018 / 0.020 | 2 (1 I, 1 P) | Accessibility |
| AX focused field → Helium | No focused element (`-25212`); focused field capabilities unmeasured | Lookup 0.015 / 0.022 | Unavailable | Accessibility alone did not yield a focused page field |
| AX tree text field → Helium | One `AXTextField` in native browser chrome; value/range 65 chars | Value 0.021/0.028; range-string 0.021/0.027 | 2 I | Accessibility; **not page-content evidence** |
| AX title → Helium | 74 chars | 0.022 / 0.038 | 2 I | Accessibility |
| AX → Safari, Slack, Discord, VS Code, Notes | Not running | — | — | Not tested |
| SCK screenshot → one Ghostty window | One 1512×1896 image | **74.063 single** (enumeration + capture) | Not applicable | Screen Recording granted in Ghostty-hosted context |
| Vision fast → that image | 1,833 chars; 61 observations | 30.695 / 137.165 | 15 (14 I, 1 P) | Screenshot access; local Vision |
| Vision accurate → same image | 1,830 chars; 63 observations | 213.025 / 349.923 | 17 (14 I, 3 P) | Same |
| tmux → attached-client pane A | Current grid: 4,300 chars, 51 lines | 3.995 / 4.243 | 11 I | Access to existing same-user tmux socket; no AX/TCC |
| tmux → attached-client pane B | Current grid: 4,048 chars, 51 lines | 3.853 / 4.216 | 27 (23 I, 4 P) | Same |
| kitty / WezTerm | Commands and checked app bundles absent | — | — | Native APIs not exercised |

### AX attribute coverage and interpretation

All times below come from the successful trusted run. Helium values refer to the **tree candidate**, not a focused field.

| Attribute / operation | Ghostty focused text | Terminal focused text | Helium chrome text |
|---|---|---|---|
| `AXRole` | `AXTextArea`, 0.016/0.019 | `AXTextArea`, 0.021/0.030 | `AXTextField`, 0.019/0.022 |
| `AXSelectedText` | No value, `-25212`, 0.014/0.018 | Empty string, 0.020/0.024 | Empty string, 0.021/0.026 |
| `AXNumberOfCharacters` | 3,156, 0.029/0.047 | 458, 0.027/0.037 | 65, 0.028/0.035 |
| `AXVisibleCharacterRange` | `(0,3156)`, 0.026/0.034 | `(0,458)`, 0.021/0.028 | `(0,65)`, 0.022/0.026 |
| `AXStringForRange` | Succeeded on reported range | Succeeded on reported range | Succeeded on reported range |
| `AXSubrole` | Unsupported | Unsupported | No value |

No nonempty selection was created, so extraction of an actual selected span remains unverified. Error distinctions matter: `-25205` = attribute unsupported, `-25211` = API disabled, `-25212` = no value; empty successful text is different from all three.

One-window scans visited 15 Ghostty nodes (13.162 ms), 11 Terminal nodes (6.739 ms), and 31 Helium nodes (9.896 ms). These times **include repeated detail probes**, not just traversal. Entire app probes took 32.793, 17.187 and 22.464 ms respectively. There was one text candidate per scanned window; no `AXWebArea` in Helium. Node/role distributions and every per-attribute timing are in `ax-results.json`.

#### Chromium / Electron accessibility activation

All three apps returned unsupported for application-level `AXManualAccessibility`. `AXEnhancedUserInterface` was readable and settable, **false in Ghostty/Terminal and true in Helium**. It was only read. Initial manual-attribute calls took 6.479 ms (Ghostty), 8.081 ms (Helium), and 4.124 ms (Terminal), illustrating cold-call cost hidden by small warm p95s.

Electron officially documents setting `AXManualAccessibility=true` to expose its tree [4]. Chromium's application implementation recognizes `AXEnhancedUserInterface`, with activation debouncing in current upstream source [5]. Chromium also documents on-demand accessibility and `--force-renderer-accessibility` [6]. **These are potential setup mechanisms, not actions performed here.** Helium already returned enhanced=true, but no causal before/after activation experiment was permitted. It is unknown whether a flag, renderer state, app-local focus, window choice or another condition explains the missing page subtree. Do not restart apps, set flags per dictation, or infer failure for every Chromium editor from this sample.

#### Ghostty ≥1.2 claim: supported, with important limits

The 1.2 release notes explicitly announce read-only macOS accessibility [1]. The installed 1.4.0 did return terminal contents without enabling enhanced UI. The **1.4.0 source** uses `cachedScreenContents` for `AXValue`, number of characters and visible range; its range is constructed from zero to `content.count`, and the cache has a 500 ms duration [2]. Upstream issue #9932 documents full-scrollback-as-visible behavior [3]. Thus this experiment verifies **readability**, not correct viewport cropping. The observed one-character difference between full value and range-string is another reason to validate boundaries; no raw content was retained to diagnose it. Swift grapheme counts versus AX UTF-16 offsets require particular care.

#### OCR and terminal-tool caveats

The Ghostty-hosted context preflighted **AX=false, Screen Recording=true**; the Terminal-hosted helper reported **AX=true, Screen Recording=false**. Permissions depend on the responsible process/launch context: do not transfer one check's result to another process.

SCK `SCContentFilter(desktopIndependentWindow:)` + `SCScreenshotManager.captureImage` avoided activation and excluded unrelated windows. The first Swift attempt failed before capture because the command-line process had not initialized AppKit/WindowServer; initializing `NSApplication.shared` fixed it without calling activate. No image was saved. Vision's cold first calls were 137.165 ms fast and 349.923 ms accurate; subsequent ranges were 27.306–31.088 and 206.066–216.522 ms. Adding the one capture suggests roughly **105 ms fast / 287 ms accurate at the measured medians**, but these sums are not measured end-to-end quantiles and exclude process startup, compilation and term extraction. OCR's extra terms versus AX do **not** establish higher recall: different windows/times, window decorations and OCR mistakes can all contribute.

Two tmux socket entries were discovered: one failed listing (stale/unavailable), one served 12 panes and two clients. Only the clients' two reported panes were captured. `$TMUX` was unset, so discovery cannot rely on the dictation process inheriting that variable. A pane's live grid is not necessarily what a user sees while in copy mode or while the outer emulator is scrolled. No new server was started. The existing `terminal.py` already resolves client tty and explicit `-S`/`-L`; reuse that targeting rather than scanning every server in production.

## Linux capability matrix — researched, NOT measured

**Latency and term yield are unmeasured for every Linux row.** No macOS numbers should be treated as Linux performance promises.

| Source × app/toolkit | Expected capability / limitation | Permission or setup | Sources |
|---|---|---|---|
| AT-SPI → GTK native text widgets | Text, caret and selection via toolkit accessibility; custom drawn widgets need explicit accessible-text implementation | Session accessibility D-Bus and app backend enabled; sandbox access can differ | [7–9] |
| AT-SPI → Qt Widgets / Qt Quick | Accessible text interfaces where app supplies them; custom canvases not guaranteed | Qt accessibility bridge and session bus; toolkit/version-specific enablement | [7], [10] |
| AT-SPI → Chromium / Electron (including Slack, Discord, VS Code variants) | Browser/renderer accessible tree when enabled; field/document implementation determines coverage | AT detection or user-configured renderer accessibility; test each app/version | [4], [6–7] |
| AT-SPI → VTE terminals | Promising terminal text source: current GTK4 VTE explicitly implements `GtkAccessibleText`; GTK3 used ATK bridge | Accessible VTE build and bus; validate viewport vs buffer | [11] |
| Hyprland IPC → all managed windows | Class/title/PID/address and focus events; **not text in the field or terminal** | Access to user's instance sockets | [12] |
| kitty remote control → exact terminal window | `kitten @ ... get-text --match id:<id> --extent screen`; plain text by default | Existing remote-control endpoint and authorization; restrict to read-only commands | [13] |
| WezTerm CLI → exact pane | `wezterm cli get-text --pane-id <id>`; main screen by default, explicit negative offsets include history | Access to appropriate mux/GUI connection; known pane ID | [14] |
| foot | `pipe-visible` / `pipe-selected` / `pipe-scrollback` export to configured command | **User keybinding/action**, not evidence of passive external text-query IPC; excluded from today's no-input workflow | [15] |
| Ghostty GTK | Do **not** assume macOS AX support transfers. Upstream discussion describes a GtkAccessibleText/AT-SPI development branch seeking upstreaming | Verify deployed build; promising PoC is not proof of released support | [16] |
| Ghostty actions | `write_screen_file` / `write_scrollback_file` can export text, but actions may copy/paste a path or open an editor | Not a demonstrated passive Linux external capture API; avoid clipboard/input side effects | [17] |
| tmux inside any emulator | `capture-pane -p -t <pane>` obtains current grid without AX/AT-SPI | Correct existing user socket and client→pane mapping | [18] |
| Wayland screenshot → OCR fallback | Portal-mediated screenshot/capture, compositor/backend-dependent targeting and consent; not universal silent per-window capture | User consent / portal capability, or compositor-specific capture policy | [19] |

### Linux capture plan

1. **Hyprland identifies the target, AT-SPI provides semantic text.** Keep `hyprctl -j activewindow` for snapshots; use `.socket2.sock` focus events (`activewindowv2`, etc.) to invalidate cached targets. IPC carries window metadata, not rendered text [12].
2. AT-SPI is a D-Bus accessibility protocol and works on Wayland independently of X11 global-window scraping [7]. Discover the accessible app, correlate PID/window where available, then focused state and `Text` interface. Prefer selection and bounded surrounding/visible text, not an entire document. Cross-process renderer trees and multiple windows make PID alone insufficient.
3. `Text.get_text(start,end)`, `get_n_selections`/`get_selection`, caret offset and `get_bounded_ranges` provide relevant primitives [8]. Bounds are implementation-dependent; verify coordinate support on Wayland and intersect with widget/viewport extents. An object's `SHOWING` state does not prove every character is visible. Respect Unicode **character** offsets, not UTF-8 byte offsets.
4. Configure client call deadlines: libatspi documents **800 ms default calls and 15 s startup grace**, far beyond hark's focus budget [9]. Use short per-call limits plus a separate overall provider deadline; subscribe to focus/text changes rather than walking the full desktop every hotkey.
5. Verify the accessibility bus and app backend in the actual Hyprland session. GTK3's bridge, GTK4's backend and Qt's bridge are different paths. Historical `GTK_MODULES` instructions are not a universal GTK4 fix. `QT_ACCESSIBILITY` / `QT_LINUX_ACCESSIBILITY_ALWAYS_ON` are documented troubleshooting knobs [7], not changes to force silently into other apps. Chromium launch flags require user-controlled setup, not restarts during capture.
6. For terminals: **exact tmux pane first**, then authorized native kitty/WezTerm API or verified VTE AT-SPI; Ghostty GTK capability-probe per deployed version. foot bindings and Ghostty export actions are optional user integrations, not default passive providers. Use repo/session context when text is unavailable.
7. OCR last, explicitly opted in. Wayland portal consent and backend support may require interaction; omit OCR rather than emulate clicks or silently capture the whole desktop. Budget and benchmark on the Linux machine before enabling it in the hotkey path.

## Recommended macOS capture strategy and guardrails

1. Keep an initialized long-lived client/helper with **its own** Accessibility trust check. Do not use Terminal automation in the product. Resolve frontmost PID/window once, and recheck before using late results.
2. Read selection, role/subrole and supported range attributes. Prefer **validated visible range → bounded `AXStringForRange`**; retain selection's existing priority. Fall back to a bounded value only for an app/role known safe, noting that AXValue's full transfer happens before client truncation. Skip secure/password fields and denylisted apps before reading text.
3. For Terminal use the range path. For Ghostty 1.4, prefer exact tmux/native verified text capture; otherwise label AX as buffer context and tightly cap/discount it. Do not assert that a tail slice equals the viewport. Avoid expensive unbounded tree scans; tree fallback must not mistake an address bar for the dictation target.
4. Work within existing focus/terminal deadlines (150/200 ms in `engine.py`). Cache target elements and feature support, retry stale elements once, keep explicit failures distinct from empty content. A 100 ms per-call timeout alone cannot bound a chain of calls. The repository's worker timeout does not cancel already-running work.
5. Keep OCR behind a per-app opt-in and separate Screen Recording preflight, and run while speech is in progress rather than after key-up. Prefer fast first; accurate only when budgets and measured gains justify it. One-window pixels still contain private material, overlays and unrelated splits. Never persist raw screenshots by default.
6. Reuse existing term extraction/ranking, deduplicate sources and cap the shortlist. Extractor counts here overstate usefulness: regex identifiers can include UI labels, log noise and OCR artifacts. `prose=True` is appropriate for prose fields; terminal/code policy may differ. Proper-name coverage needs separate evaluation, not a fabricated NAME yield.
7. Scrub/denylist **all** new text sources before logging or transmission. Existing `engine.scrub()` is currently applied to agent replies, not all context, and is only a token heuristic—not a privacy boundary. Do not log raw AX values, window titles, screenshots, terminal output or clipboard contents. Context is untrusted data, not instructions for a cleanup model.

## Artifacts and reproduction

All deliverables are in this directory:

- `probe_ax.py`: PID-only AX reads, attribute timings and sanitized term metrics.
- `ax-results.json`: successful Terminal-hosted trusted run.
- `ax-untrusted-results.json`: direct Ghostty-hosted denial measurements and permission split.
- `probe_ocr.swift` + `summarize_ocr.py`: one-window SCK capture, local Vision, counts-only output.
- `ocr-results.json`: five fast/five accurate measurements on the one captured image.
- `probe_terminal_tools.py` + `terminal-tools-results.json`: availability and existing tmux current-grid measurements.

From the project root, using the existing environment (no dependency installation):

```sh
export PYTHONDONTWRITEBYTECODE=1
.venv/bin/python docs/research/context-capture/probe_ax.py
.venv/bin/python docs/research/context-capture/probe_terminal_tools.py
# IMPORTANT: keep raw OCR stdout in this anonymous pipe, never a file/log.
set -o pipefail
swift docs/research/context-capture/probe_ocr.swift |
  .venv/bin/python docs/research/context-capture/summarize_ocr.py
```

The AX command needs a trusted launch context to reproduce successful reads; running directly here intentionally gives denial. The OCR program requires ScreenCaptureKit screenshot APIs (macOS 14+) and existing Screen Recording permission. It preflights without prompting and skips if denied or no on-screen Ghostty window exists. Re-running captures a new window sample; it does not reproduce identical content. Do not start apps or change permissions solely to make the sample succeed without user approval.

Validation performed: successful trusted/untrusted AX runs, successful SCK/Vision run, tmux reads, Python AST parsing and JSON parsing. No production tests or inference workloads were needed. Remaining coverage gaps: real selected text, real focused browser/page/editor input, long Terminal scrollback, stalled providers, viewport ground truth, proper-name recall, Chromium activation before/after, other absent applications, and all Linux latency/yield measurements.

## Sources

Sources retrieved 2026-11-11; versioned source is distinguished from moving upstream documentation. Research claims are not local test results.

1. [Ghostty 1.2.0 release notes — accessibility](https://ghostty.org/docs/install/release-notes/1-2-0).
2. [Ghostty **v1.4.0** SurfaceView_AppKit.swift](https://raw.githubusercontent.com/ghostty-org/ghostty/v1.4.0/macos/Sources/Ghostty/Surface%20View/SurfaceView_AppKit.swift): cached text, visible range, string ranges and 500 ms cache.
3. [Ghostty issue #9932 — incorrect visible range / missing geometry](https://github.com/ghostty-org/ghostty/issues/9932). Versioned code, not issue status alone, informs the installed-version warning.
4. [Electron accessibility guide](https://www.electronjs.org/docs/latest/tutorial/accessibility): automatic detection and `AXManualAccessibility` example.
5. [Chromium application macOS implementation, current upstream](https://raw.githubusercontent.com/chromium/chromium/main/chrome/browser/chrome_browser_application_mac.mm): enhanced-UI activation/debounce; not asserted to be Helium's exact revision.
6. [Chromium accessibility overview](https://chromium.googlesource.com/chromium/src/+/HEAD/docs/accessibility/overview.md): native APIs, multiprocess trees, on-demand activation and renderer flags.
7. [freedesktop AT-SPI2](https://wiki.freedesktop.org/www/Accessibility/AT-SPI2/): D-Bus protocol, enablement, Qt knobs and Wayland note. Some setup examples are historical/X11-specific.
8. [AT-SPI Text interface](https://docs.gtk.org/atspi2/iface.Text.html), [get_text](https://docs.gtk.org/atspi2/method.Text.get_text.html), [get_bounded_ranges](https://docs.gtk.org/atspi2/method.Text.get_bounded_ranges.html).
9. [AT-SPI set_timeout](https://docs.gtk.org/atspi2/func.set_timeout.html); [GTK4 accessibility](https://docs.gtk.org/gtk4/section-accessibility.html), including AT-SPI platform mapping.
10. [Qt accessibility classes](https://doc.qt.io/qt-6/accessibility.html): accessible text and custom-widget interfaces; Linux bridge setup also covered by [7].
11. [Current VTE GTK4 Terminal API](https://gnome.pages.gitlab.gnome.org/vte/gtk4/class.Terminal.html): explicitly implements `GtkAccessibleText`; [GTK4 accessibility architecture transition](https://blogs.gnome.org/gtk/2020/10/21/accessibility-in-gtk-4/).
12. [Hyprland IPC](https://wiki.hypr.land/IPC/): command/event sockets and focus events.
13. [kitty remote control](https://sw.kovidgoyal.net/kitty/remote-control/): `get-text`, screen extent, matching and granular authorization. Never use `--clear-selection` for a read-only capture.
14. [WezTerm get-text](https://wezterm.org/cli/cli/get-text.html): pane targeting and main-screen versus scrollback semantics.
15. [foot.ini manual](https://manpages.debian.org/trixie/foot/foot.ini.5.en.html): pipe-visible/scrollback/selected keybindings.
16. [Ghostty GTK screen-reader/AT-SPI discussion #13746](https://github.com/ghostty-org/ghostty/discussions/13746): explicitly describes a PoC/development branch and proposed upstreaming, not a released-support guarantee.
17. [Ghostty keybinding action reference](https://ghostty.org/docs/config/keybind/reference): screen/scrollback file export actions and clipboard/paste/open consequences.
18. [tmux manual source](https://raw.githubusercontent.com/tmux/tmux/master/tmux.1): capture-pane and targeting. Local executable tested was 3.8.
19. [XDG Screenshot portal](https://flatpak.github.io/xdg-desktop-portal/docs/doc-org.freedesktop.portal.Screenshot.html): screenshot requests/options and portal-mediated response.
