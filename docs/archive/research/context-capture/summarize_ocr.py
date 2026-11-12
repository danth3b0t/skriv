#!/usr/bin/env python3
"""Consume raw OCR in-memory over stdin; print only aggregate metrics."""
import json
import sys
from probe_ax import metrics, timing

raw = json.load(sys.stdin)
if 'results' not in raw:
    print(json.dumps(raw))
else:
    out = {k: v for k, v in raw.items() if k != 'results'}
    out['modes'] = {}
    for mode in ['fast', 'accurate']:
        runs = [r for r in raw['results'] if r['mode'] == mode]
        out['modes'][mode] = {'latency': timing([r['ms'] for r in runs]),
                              'runs': [{'trial': r['trial'], 'ms': round(r['ms'], 3),
                                        'observations': r['observations'], 'metrics': metrics(r['text'])} for r in runs]}
    print(json.dumps(out, indent=2))
