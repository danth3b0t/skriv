"""Server configuration: TOML file, overridden by CLI flags. See hark-server.example.toml."""

from __future__ import annotations

import argparse
import dataclasses
import ipaddress
import socket
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from collections.abc import Mapping

    from _typeshed import DataclassInstance


class ConfigError(ValueError):
    pass


@dataclass
class TlsConfig:
    cert: str = ""
    key: str = ""
    client_ca: str = ""  # set => mTLS: clients must present a cert signed by this CA


@dataclass
class CleanupConfig:
    base_url: str = ""  # OpenAI-compatible base, e.g. http://127.0.0.1:8080/v1; "" => disabled
    model: str = ""
    engine: str = "s1"  # s1 (S1-mini full rewrite -> edits) | edits (JSON edit list)
    api_key_env: str = ""  # name of an env var holding the LLM server's bearer token
    default_budget_ms: int = 400
    max_input_bytes: int = 4000
    max_edits: int = 16


@dataclass
class ServerConfig:
    listen: str = "127.0.0.1:50051"
    name: str = field(default_factory=socket.gethostname)
    accelerator: str = ""
    max_concurrent_sessions: int = 4
    max_audio_ms: int = 300_000
    default_finalize_budget_ms: int = 1000
    eager_end: bool = True
    warmup_on_start: bool = True
    allow_insecure_non_local: bool = False
    tls: TlsConfig = field(default_factory=TlsConfig)


@dataclass
class BackendConfig:
    kind: str = "mock"  # mock | parakeet_mlx | nemo_gpupb
    model: str = ""  # backend-specific model name / path ("" => backend default)
    model_id: str = ""  # id clients use in SessionStart.model_id ("" => derived)
    options: dict[str, object] = field(default_factory=dict)  # backend constructor kwargs


@dataclass
class Config:
    server: ServerConfig = field(default_factory=ServerConfig)
    backend: BackendConfig = field(default_factory=BackendConfig)
    cleanup: CleanupConfig = field(default_factory=CleanupConfig)
    log_level: str = "INFO"


def _fill(
    obj: DataclassInstance | type[DataclassInstance], data: Mapping[str, object], where: str
) -> None:
    names = {f.name: f for f in dataclasses.fields(obj)}
    for key, value in data.items():
        if key not in names:
            raise ConfigError(f"unknown config key {where}{key}")
        current = cast("object", getattr(obj, key))
        if dataclasses.is_dataclass(current):
            if not isinstance(value, dict):
                raise ConfigError(f"{where}{key} must be a table")
            _fill(current, cast("dict[str, object]", value), f"{where}{key}.")
        else:
            setattr(obj, key, value)


def load(path: str | None) -> Config:
    cfg = Config()
    if path:
        with Path(path).open("rb") as f:
            _fill(cfg, tomllib.load(f), "")
    return cfg


def is_loopback(listen: str) -> bool:
    if listen.startswith("unix:"):
        return True
    host = listen.rsplit(":", 1)[0].strip("[]")
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def validate(cfg: Config) -> None:
    tls = cfg.server.tls
    if bool(tls.cert) != bool(tls.key):
        raise ConfigError("server.tls.cert and server.tls.key must be set together")
    if tls.client_ca and not tls.cert:
        raise ConfigError("server.tls.client_ca (mTLS) requires server.tls.cert/key")
    if (
        not tls.cert
        and not is_loopback(cfg.server.listen)
        and not cfg.server.allow_insecure_non_local
    ):
        raise ConfigError(
            f"refusing plaintext on non-loopback address {cfg.server.listen!r}: configure "
            "server.tls (mTLS recommended) or set server.allow_insecure_non_local = true"
        )
    if cfg.cleanup.engine not in ("s1", "edits"):
        raise ConfigError("cleanup.engine must be 's1' or 'edits'")
    if cfg.server.max_concurrent_sessions < 1:
        raise ConfigError("server.max_concurrent_sessions must be >= 1")


class _Args(argparse.Namespace):
    # Annotations only: argparse fills in the defaults for attributes that are not set.
    config: str | None
    listen: str | None
    backend: str | None
    model: str | None
    model_id: str | None
    tls_cert: str | None
    tls_key: str | None
    tls_client_ca: str | None
    allow_insecure_non_local: bool | None
    no_warmup: bool
    cleanup_url: str | None
    cleanup_model: str | None
    cleanup_engine: str | None
    log_level: str | None


def parse_args(argv: list[str] | None = None) -> Config:
    p = argparse.ArgumentParser(prog="hark-server", description="hark.v1 gRPC inference server")
    p.add_argument("--config", help="TOML config file (CLI flags override it)")
    p.add_argument("--listen", help="host:port or unix:/path (default 127.0.0.1:50051)")
    p.add_argument("--backend", choices=["mock", "parakeet_mlx", "nemo_gpupb"])
    p.add_argument("--model", help="backend model name/path")
    p.add_argument("--model-id", help="model id advertised to clients")
    p.add_argument("--tls-cert")
    p.add_argument("--tls-key")
    p.add_argument("--tls-client-ca", help="require client certs signed by this CA (mTLS)")
    p.add_argument("--allow-insecure-non-local", action="store_true", default=None)
    p.add_argument("--no-warmup", action="store_true", help="do not load/warm the model at start")
    p.add_argument("--cleanup-url", help="OpenAI-compatible base URL for TextCleanup")
    p.add_argument("--cleanup-model")
    p.add_argument("--cleanup-engine", choices=["s1", "edits"],
                   help="s1: S1-mini full rewrite (default); edits: JSON edit list")  # fmt: skip
    p.add_argument("--log-level")
    a = p.parse_args(argv, namespace=_Args())

    cfg = load(a.config)
    overrides: list[tuple[str | bool | None, object, str]] = [
        (a.listen, cfg.server, "listen"),
        (a.backend, cfg.backend, "kind"),
        (a.model, cfg.backend, "model"),
        (a.model_id, cfg.backend, "model_id"),
        (a.tls_cert, cfg.server.tls, "cert"),
        (a.tls_key, cfg.server.tls, "key"),
        (a.tls_client_ca, cfg.server.tls, "client_ca"),
        (a.allow_insecure_non_local, cfg.server, "allow_insecure_non_local"),
        (a.cleanup_url, cfg.cleanup, "base_url"),
        (a.cleanup_model, cfg.cleanup, "model"),
        (a.cleanup_engine, cfg.cleanup, "engine"),
        (a.log_level, cfg, "log_level"),
    ]
    for value, obj, name in overrides:
        if value is not None:
            setattr(obj, name, value)
    if a.no_warmup:
        cfg.server.warmup_on_start = False
    validate(cfg)
    return cfg
