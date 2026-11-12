# AUR packaging

`skriv-git/PKGBUILD` builds skriv from git: the client daemon (`skriv`), the inference server (`skriv-server`), the
systemd user units, the Omarchy adapter (dictation backend, `skriv.shell` bar plugin, `skriv-panel`) and the helper
scripts. User-facing setup: [docs/linux.md](../../docs/linux.md).

## Build, check, publish

```sh
cd packaging/aur/skriv-git
SKRIV_REPO="file://$PWD/../../..#branch=main" makepkg -si   # local clone; without SKRIV_REPO: the url= repo
namcap PKGBUILD && namcap skriv-git-*.pkg.tar.zst
makepkg --printsrcinfo > .SRCINFO                           # after every PKGBUILD change; commit both
```

Publishing (once the repository is public and has a LICENSE): set `url`, `license` (and install the licence
file to `/usr/share/licenses/skriv-git/`), the Maintainer line, regenerate `.SRCINFO`, then push `PKGBUILD` and
`.SRCINFO` to `ssh://aur@aur.archlinux.org/skriv-git.git`. `SKRIV_REPO` exists only for building before that.

## Decisions

**`-git` only, for now.** There are no tags or release tarballs yet. `pkgver()` follows the VCS guidelines:
`git describe` once a `v*` tag exists (commitizen's `tag_format`), otherwise `<pyproject version>.r<commits>.g<hash>`.
A release package `skriv` (tarball + sha256) follows the first tag; `provides`/`conflicts` already name it.

**Python dependencies: a private CPython 3.12 + venv in `/usr/lib/skriv`, synced from `uv.lock`.** The standard
route (one Arch package per Python dependency, built with `python-build`/`python-installer` against the system
Python) does not work for skriv today:

- skriv requires Python 3.12 (`requires-python = ">=3.12,<3.13"`); Arch ships 3.14.
- `[extra]` has `python-grpcio` 1.80, skriv needs ≥ 1.85; `python-sounddevice` is AUR-only; the rest would be a
  chain of AUR packages that all need rebuilding at every Arch Python bump.

So `build()` does what Arch's own app-with-venv packages and most AUR "-venv" packages do, with stronger pinning:
`uv python install 3.12` (python-build-standalone, sha256-checked by uv; `UV_PYTHON_PREFERENCE=only-managed`
so the system 3.14 is never picked), then `uv sync --frozen --no-dev --no-editable --all-packages --extra gi`
into a staged venv: every wheel/sdist is pinned by version **and hash** in the committed `uv.lock`; nothing is
resolved at build time. `package()` moves the tree to `/usr/lib/skriv`, rewrites the build paths (venv
`pyvenv.cfg`, script shebangs, the interpreter's sysconfig data), drops Tk/IDLE (unused; their libraries
carry a `/tools/deps/lib` RUNPATH), byte-compiles with the installed paths and fails if any file still names
`$srcdir`.

Costs, accepted: ~59 MiB download / ~190 MiB installed; the build needs the network (normal for VCS packages,
but the package is not reproducible offline); security fixes in bundled wheels arrive by bumping `uv.lock` and
rebuilding, not through `pacman -Syu`. PyGObject and pycairo have no Linux wheels, so they are built from their
(hash-pinned) sdists against the system GLib/cairo/gobject-introspection; their build backends (meson-python,
meson, ninja) come from PyPI inside uv's isolated build environment and are **not** hash-pinned.

**One package, not `skriv` + `skriv-server`.** In the target setup both machines run `skriv-server` (the desktop on
the GPU, the laptop as the local CPU fallback), the server adds ~1 MB of Python to a runtime the client needs
anyway, and its units are inert until enabled. Separate venvs would ship the ~100 MB interpreter twice; a shared
runtime package would be a third package for no user-visible gain. The heavy, hardware-specific parts are
already optional: llama.cpp comes from the repos (`llama-cpp` + `ggml-cuda` / `ggml-vulkan` as optdepends,
launched by `skriv-llama.service`), models are downloaded per user (`/usr/lib/skriv/scripts/fetch-models` →
`~/.local/share/skriv/models`). The Linux ASR engine, transcribe.cpp (`transcribe_cpp` backend), is a native
library that `scripts/fetch-transcribe` builds in a checkout; it is not packaged yet.

**`options=('!strip' '!debug')`.** The ELF files are prebuilt manylinux wheels (edited by auditwheel/patchelf,
which `strip` can corrupt) and python-build-standalone; there is no source of ours to debug.

**Systemd units are user units** (`/usr/lib/systemd/user`), rendered by `skriv_client/service/linux.py`
(`test_packaged_units_are_the_rendered_ones` pins `packaging/systemd/` to it). Enabling is per user:
`skriv service install [client|server|llama]`.

## namcap

`namcap PKGBUILD`: clean. `namcap` on the package, built in an `archlinux:latest` container (x86_64):

| Kind | Why it stays |
|---|---|
| E: `LicenseRef-TODO` needs a licence file | the repository has no LICENSE yet: blocker for publishing |
| E: dependency `python` / `python-setuptools` detected | namcap maps every `.py` import to Arch packages; the package brings its own interpreter, and setuptools is only imported by build helpers in cffi and numpy's tests |
| W: dependency included but may not be needed: bash, curl, portaudio, wl-clipboard | used by exec (`/bin/bash` scripts, `curl` in `skriv-llama.service`, `wl-copy`) or `ctypes` (PortAudio), which namcap cannot see |
| W: referenced python module / unused shared library / unstripped / lacks FULL RELRO | properties of the upstream wheels and interpreter; `!strip` is deliberate |

Also verified in that container: `pacman -U`, `skriv ctl status` (exit 2 without a daemon), PyGObject with the
Gtk 4, Gtk4LayerShell and Atspi typelibs, `skriv once --dry-run` against `skriv-server --backend mock`,
`skriv service install --dry-run client server llama` (uses the package's units), `llama-up` resolving the XDG
models dir, and the units' syntax (`systemd-analyze verify`: no unknown keys; its executable checks cannot run
under emulation). Not verified: the units under a real user manager, a Wayland session, audio, a GPU.
