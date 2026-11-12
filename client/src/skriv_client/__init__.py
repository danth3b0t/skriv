"""skriv POC client."""

import os

__version__ = "0.1.0"

# grpcio enables gRPC core's fork support by default. Its pthread_atfork handlers
# then pause gRPC's threads on every fork() in this process (afplay for the key-up
# sound, git and osascript in the context engine, wl-copy...), which held
# EndOfAudio in the request queue for up to ~1.2 s. skriv's children exec right
# away and never use gRPC, so the handlers buy nothing. Must be set before grpc
# is imported; set here because every client module is imported through this package.
os.environ.setdefault("GRPC_ENABLE_FORK_SUPPORT", "false")
