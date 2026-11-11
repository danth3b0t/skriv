"""Startup configuration: transport security defaults, backends, generated stubs."""

from __future__ import annotations

import filecmp
import importlib.util
import subprocess
from pathlib import Path

import pytest

from hark_server.backends import BackendUnavailable, create_backend
from hark_server.config import ConfigError, parse_args

SERVER = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    ("args", "error"),
    [
        (["--listen", "127.0.0.1:50051"], None),
        (["--listen", "[::1]:50051"], None),
        (["--listen", "localhost:50051"], None),
        (["--listen", "unix:/tmp/hark.sock"], None),
        (["--listen", "0.0.0.0:50051"], "refusing plaintext"),
        (["--listen", "100.64.1.2:50051"], "refusing plaintext"),
        (["--listen", "0.0.0.0:50051", "--allow-insecure-non-local"], None),
        (["--listen", "0.0.0.0:50051", "--tls-cert", "c.pem", "--tls-key", "k.pem"], None),
        (["--tls-cert", "c.pem"], "set together"),
        (["--tls-client-ca", "ca.pem"], "requires"),
    ],
)
def test_plaintext_only_on_loopback_unless_overridden(args: list[str], error: str | None):
    if error is None:
        parse_args(args)
    else:
        with pytest.raises(ConfigError, match=error):
            parse_args(args)


def test_toml_config_rejects_unknown_keys(tmp_path: Path):
    good = tmp_path / "ok.toml"
    good.write_text(
        '[server]\nlisten = "127.0.0.1:6000"\n[backend]\nkind = "mock"\n'
        "[backend.options]\nword_ms = 100\n"
    )
    cfg = parse_args(["--config", str(good), "--backend", "parakeet_mlx"])
    assert cfg.server.listen == "127.0.0.1:6000"
    assert cfg.backend.kind == "parakeet_mlx"  # CLI overrides TOML
    assert cfg.backend.options == {"word_ms": 100}

    bad = tmp_path / "bad.toml"
    bad.write_text('[server]\nlisten_addr = "127.0.0.1:6000"\n')
    with pytest.raises(ConfigError, match=r"server\.listen_addr"):
        parse_args(["--config", str(bad)])


def test_nemo_backend_fails_clearly_without_nemo():
    if importlib.util.find_spec("nemo") is not None:
        pytest.skip("NeMo is installed on this host")
    with pytest.raises(BackendUnavailable, match="NeMo"):
        create_backend("nemo_gpupb", "", "", {})


def test_checked_in_stubs_match_proto(tmp_path: Path):
    gen_proto = SERVER / "scripts" / "gen-proto"
    subprocess.run([str(gen_proto), str(tmp_path)], check=True)  # noqa: S603 - repo script, fixed argv
    for name in ("hark_pb2.py", "hark_pb2.pyi", "hark_pb2_grpc.py"):
        assert filecmp.cmp(
            tmp_path / "hark/v1" / name, SERVER / "src/hark/v1" / name, shallow=False
        ), name
