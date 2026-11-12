#!/usr/bin/env python3
"""Read-only, PID-targeted AX probe. Emits metrics only; never activates apps.
Run with hark's Python and PYTHONDONTWRITEBYTECODE=1. No AX setters except
client-side messaging timeouts; enabling Chromium accessibility is NOT attempted.
"""
import collections
import json
import math
import pathlib
import plistlib
import sys
import time
import traceback

import AppKit
import ApplicationServices as AX
import Quartz

ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'client/src'))
from hark_client.context.aliases import extract_terms

REPEATS = 20
TIMEOUT = 0.10
WANTED = {'Ghostty', 'Terminal', 'Helium', 'Safari', 'Slack', 'Discord', 'Code', 'Visual Studio Code', 'Notes'}


def metrics(text):
    if not isinstance(text, str):
        return {'text': False}
    terms = extract_terms(text)
    return {'chars': len(text), 'lines': len(text.splitlines()), 'terms': len(terms),
            'kinds': dict(collections.Counter(str(kind) for _, kind in terms))}


def timing(values):
    s = sorted(values)
    return {'n': len(s), 'p50_ms': round(s[math.ceil(len(s)*.50)-1], 3),
            'p95_ms': round(s[math.ceil(len(s)*.95)-1], 3), 'max_ms': round(s[-1], 3),
            'first_ms': round(values[0], 3)}


def raw(el, attr):
    return AX.AXUIElementCopyAttributeValue(el, attr, None)


def bounded(el):
    AX.AXUIElementSetMessagingTimeout(el, TIMEOUT)
    return el


def bench(fn):
    times, errors, val = [], [], None
    for _ in range(REPEATS):
        t = time.perf_counter()
        err, val = fn()
        times.append((time.perf_counter()-t)*1000)
        errors.append(int(err))
        if err == -25202:  # Stop retrying a hung provider.
            break
    return {'latency': timing(times), 'errors': dict(collections.Counter(errors))}, val


def detail(el):
    bounded(el)
    out = {}
    for attr in ['AXRole', 'AXSubrole', 'AXValue', 'AXSelectedText', 'AXNumberOfCharacters', 'AXVisibleCharacterRange']:
        record, value = bench(lambda: raw(el, attr))
        if attr in ('AXRole', 'AXSubrole'):
            record['role'] = str(value) if value else None
        elif attr == 'AXNumberOfCharacters':
            record['count'] = int(value) if value is not None else None
        elif attr == 'AXVisibleCharacterRange':
            if value is not None:
                ok, rng = AX.AXValueGetValue(value, AX.kAXValueCFRangeType, None)
                if ok:
                    record['range'] = {'location': rng[0], 'length': rng[1]}
                    param = AX.AXValueCreate(AX.kAXValueCFRangeType, rng)
                    result, text = bench(lambda: AX.AXUIElementCopyParameterizedAttributeValue(el, 'AXStringForRange', param, None))
                    result['metrics'] = metrics(text)
                    out['AXStringForRange_visible'] = result
        else:
            record['metrics'] = metrics(value)
        out[attr] = record
    # Check a bounded prefix separately when there is no visible-range support.
    if 'AXStringForRange_visible' not in out:
        count = out['AXNumberOfCharacters']['count']
        if count is not None and count > 0:
            param = AX.AXValueCreate(AX.kAXValueCFRangeType, (0, min(count, 2048)))
            result, text = bench(lambda: AX.AXUIElementCopyParameterizedAttributeValue(el, 'AXStringForRange', param, None))
            result['metrics'] = metrics(text)
            out['AXStringForRange_prefix_NOT_visible'] = result
    return out


def app_probe(app):
    start = time.perf_counter()
    root = bounded(AX.AXUIElementCreateApplication(app.processIdentifier()))
    bundle = pathlib.Path(str(app.bundleURL().path())) / 'Contents/Info.plist'
    out = {'app': str(app.localizedName()), 'version': plistlib.loads(bundle.read_bytes()).get('CFBundleShortVersionString')}
    for flag in ['AXManualAccessibility', 'AXEnhancedUserInterface']:
        r, v = bench(lambda: raw(root, flag))
        r['boolean'] = bool(v) if v is not None else None
        err, supported = AX.AXUIElementIsAttributeSettable(root, flag, None)
        r['settable_error'] = int(err)
        r['settable'] = bool(supported)
        out[flag] = r
    r, focused = bench(lambda: raw(root, 'AXFocusedUIElement'))
    out['focused_lookup'] = r
    if focused is not None:
        out['focused'] = detail(focused)
    r, win = bench(lambda: raw(root, 'AXFocusedWindow'))
    out['window_lookup'] = r
    if win is not None:
        bounded(win)
        r, title = bench(lambda: raw(win, 'AXTitle'))
        r['metrics'] = metrics(title)
        out['window_title'] = r
    _, wins = raw(root, 'AXWindows')
    out['window_count'] = len(wins or [])
    # One current app-local window, NOT all tabs/windows; no focus changes.
    queue = collections.deque([(win if win is not None else (wins[0] if wins else root), 0)])
    roles = collections.Counter()
    candidates, nodes = [], 0
    scan = time.perf_counter()
    while queue and nodes < 160 and time.perf_counter()-scan < 5:
        el, depth = queue.popleft()
        bounded(el)
        nodes += 1
        err, role = raw(el, 'AXRole')
        role = str(role) if role is not None else 'unavailable'
        roles[role] += 1
        if role in {'AXTextArea', 'AXTextField', 'AXComboBox', 'AXWebArea'} and len(candidates) < 8:
            candidates.append(detail(el))
        if depth < 12:
            _, kids = raw(el, 'AXChildren')
            if kids:
                queue.extend((child, depth+1) for child in kids[:160])
    out['tree'] = {'nodes': nodes, 'roles': dict(roles), 'bounded_or_incomplete': bool(queue),
                   'scan_ms': round((time.perf_counter()-scan)*1000, 3), 'text_candidates': candidates}
    out['total_ms'] = round((time.perf_counter()-start)*1000, 3)
    return out


def main():
    apps = [a for a in AppKit.NSWorkspace.sharedWorkspace().runningApplications() if str(a.localizedName()) in WANTED]
    out = {'AX_trusted': bool(AX.AXIsProcessTrusted()), 'screen_recording': bool(Quartz.CGPreflightScreenCaptureAccess()),
           'timeout_s': TIMEOUT, 'repeats': REPEATS, 'flags_written': False, 'apps': [],
           'not_running': sorted(WANTED - {str(a.localizedName()) for a in apps})}
    for app in apps:
        try:
            out['apps'].append(app_probe(app))
        except Exception as e:
            out['apps'].append({'app': str(app.localizedName()), 'exception_type': type(e).__name__, 'frames': [{'function': f.name, 'line': f.lineno} for f in traceback.extract_tb(e.__traceback__)]})
    print(json.dumps(out, indent=2))

if __name__ == '__main__':
    main()
