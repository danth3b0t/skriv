#!/bin/bash
# usage: sweep.sh NAME MODEL "toml [backend.options] lines" client-config...
# Starts hark-server from the worktree on :50064 with the options, runs the eval per client config.
set -u
mkdir -p /tmp/w3
cd /Users/danth3b0t/projects/hark-w3-recognition
D=docs/bench/w3-recognition
name=$1; model=$2; opts=$3; shift 3
cat > /tmp/w3/srv-$name.toml <<EOF
[backend]
kind = "parakeet_mlx"
model = "$model"
[backend.options]
$opts
[cleanup]
base_url = "http://127.0.0.1:8080/v1"
model = "s1-mini-q4_k_m"
EOF
[ -f /tmp/w3/srv.pid ] && kill "$(cat /tmp/w3/srv.pid)" 2>/dev/null && sleep 2
uv run hark-server --config /tmp/w3/srv-$name.toml --listen 127.0.0.1:50064 > /tmp/w3/srv-$name.log 2>&1 &
echo $! > /tmp/w3/srv.pid
for i in $(seq 60); do grep -q "model ready" /tmp/w3/srv-$name.log && break; sleep 1; done
grep "model ready" /tmp/w3/srv-$name.log || { tail /tmp/w3/srv-$name.log; exit 1; }
for cfg in "$@"; do
  c=$(basename "$cfg" .toml)
  out=$name; [ "$c" != baseline ] && out=$name--$c
  uv run python docs/bench/w3-recognition/run.py --config "$cfg" --server 127.0.0.1:50064 eval run --set eval/dev-v1 \
    --report $D/$out.json > $D/$out.txt 2>&1
  echo "$out: $(tail -2 $D/$out.txt | head -1)"
done
