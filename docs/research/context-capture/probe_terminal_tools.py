#!/usr/bin/env python3
"""Read-only tmux current-grid probe. No pane creation/selection or scrollback.
Discover existing default/custom tmux sockets; stdout contains no socket/pane names.
"""
import json
import os
import pathlib
import shutil
import stat
import subprocess
import time
from probe_ax import metrics, timing


def run(argv):
    return subprocess.run(argv, capture_output=True, text=True, timeout=1, check=False)


out = {'available': {k: bool(shutil.which(k)) for k in ['tmux', 'kitty', 'wezterm']},
       'app_bundles': {k: (pathlib.Path('/Applications') / (k + '.app')).exists() for k in ['kitty', 'WezTerm']},
       'tmux_env_present': bool(os.environ.get('TMUX')), 'servers': []}
if out['available']['tmux']:
    out['tmux_version'] = run(['tmux', '-V']).stdout.strip()
    # Only inspect existing owned sockets. Never start a tmux server.
    base = pathlib.Path(os.environ.get('TMUX_TMPDIR', '/tmp')) / f'tmux-{os.getuid()}'
    sockets = [p for p in base.glob('*') if stat.S_ISSOCK(p.stat().st_mode) and p.stat().st_uid == os.getuid()]
    if os.environ.get('TMUX'):
        p = pathlib.Path(os.environ['TMUX'].split(',')[0])
        if p not in sockets:
            sockets.append(p)
    out['socket_count'] = len(sockets)
    for sock in sockets:
        prefix = ['tmux', '-S', str(sock)]
        clients = run(prefix + ['list-clients', '-F', '#{pane_id}'])
        listing = run(prefix + ['list-panes', '-a', '-F', '#{pane_id}\t#{pane_active}\t#{window_active}\t#{session_attached}'])
        rows = [r.split('\t') for r in listing.stdout.splitlines()]
        server = {'list_exit': listing.returncode, 'pane_count': len(rows),
                  'client_count': len(clients.stdout.splitlines()), 'captures': []}
        targets = list(dict.fromkeys(clients.stdout.splitlines()))
        source = 'client_reported_pane'
        if not targets:
            targets = [r[0] for r in rows if len(r)==4 and r[1:3]==['1','1']][:1]
            source = 'unattached_active_pane_NOT_verified_visible'
        for target in targets[:3]:
            times = []
            result = None
            for _ in range(20):
                t = time.perf_counter()
                result = run(prefix + ['capture-pane', '-p', '-t', target])
                times.append((time.perf_counter()-t)*1000)
            server['captures'].append({'target_source': source, 'exit': result.returncode,
                                       'latency': timing(times), 'metrics': metrics(result.stdout)})
        out['servers'].append(server)
print(json.dumps(out, indent=2))
