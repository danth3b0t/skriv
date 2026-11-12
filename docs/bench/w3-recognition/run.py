"""`hark ... eval run` with each clip's server timings (backend_ms), model id and anchor text
added to the report as "server" (bench probe for 2026-11-13-w3-recognition.md). Env: W3_TERM_WEIGHT
="git=0.65" sets that canonical's BiasTerm weight; W3_TERMINAL_WEIGHT=0.65 the weight of the
terminal base commands (those still at words.TERMINAL_WEIGHT).

    uv run python docs/bench/w3-recognition/run.py --config X --server Y eval run --set eval/dev-v1 --report R
"""

import os
import sys

from google.protobuf.json_format import MessageToDict

from hark_client import asr, evalset, pipeline
from hark_client.__main__ import main
from hark_client.context import words

_last: dict[str, object] = {}
_orig_gate = pipeline.Utterance._gate  # pyright: ignore[reportPrivateUsage]


def _gate(self, final, hyp, res):  # noqa: ANN001, ANN202
    _last.clear()
    _last["timings"] = MessageToDict(final.timings)
    _last["model_id"] = final.model_id
    _last["language"] = final.detected_language
    _last["anchor"] = final.anchor.text if final.HasField("anchor") else None
    return _orig_gate(self, final, hyp, res)


_orig_report = evalset.report


def _report(s, res):  # noqa: ANN001, ANN202
    out = _orig_report(s, res)
    out["server"] = dict(_last)
    _last.clear()
    return out


# W3_TERM_WEIGHT="git=0.8": that canonical's weight in every BiasList (boost probe)
_override = dict(kv.split("=") for kv in os.environ.get("W3_TERM_WEIGHT", "").split(",") if kv)
_orig_bias_list = asr.bias_list


def _bias_list(terms):  # noqa: ANN001, ANN202
    out = _orig_bias_list(terms)
    for t in out.terms:
        if t.canonical in _override:
            t.weight = float(_override[t.canonical])
        tw = os.environ.get("W3_TERMINAL_WEIGHT")
        if (
            tw
            and t.canonical in words.TERMINAL_COMMANDS
            and abs(t.weight - words.TERMINAL_WEIGHT) < 1e-6
        ):
            t.weight = float(tw)
    return out


asr.bias_list = _bias_list
pipeline.Utterance._gate = _gate  # pyright: ignore[reportPrivateUsage]
evalset.report = _report
sys.argv[0] = "hark"
sys.exit(main())
