"""Build 80 cases. Private inputs are referenced by index, never published in results."""
import json, pathlib, hashlib
P=pathlib.Path(__file__).parent; ROOT=P.parents[3]
rows=[]
for fn in ['cleanup_eval.jsonl','cleanup_eval_real.jsonl']:
 data=[json.loads(l) for l in (ROOT/'server/tests/data'/fn).read_text().splitlines()]
 if 'real' in fn:
  ids=['r-negation-1','r-filler-1','r-filler-2','r-filler-3','r-filler-4','r-selfcorr-1','r-filler-5','r-czech-1','r-ident-2','r-clean-10']
  data=[r for r in data if r['id'] in ids]
 for r in data:
  r['source']='existing-real' if 'real' in fn else 'existing'; r['private']='real' in fn
  # An existing alternative deletes a meaningful negation, outside this task's policy.
  if r['id']=='r-filler-1': r['expected']=r['expected'][:2]
  rows.append(r)
history=[json.loads(l) for l in open('/tmp/hark-history-snapshot.jsonl')]
# Hand-reviewed gold. No ASR hallucination correction, synonyms, contraction expansion.
specs={int(k):v for k,v in json.loads((P/'private-golds.json').read_text()).items()}
for idx,(cat,gold) in specs.items():
 text=history[idx]['raw_text']
 rows.append(dict(id=f'h-{idx}',category=cat,profile='prose',source='snapshot',private=True,text=text,expected=[gold or text],history_index=idx))
new=[
 ('self_correction','I want it written in C, uh no, in Rust.','I want it written in Rust.'),
 ('self_correction','Send it on Monday, no wait, on Friday, I mean on Thursday.','Send it on Thursday.'),
 ('self_correction','Use red, or rather blue, for the header.','Use blue for the header.'),
 ('clean','I like Rust and I mean it. There is no wait time.','I like Rust and I mean it. There is no wait time.'),
 ('parentheses','The worker which is optional runs locally','The worker (which is optional) runs locally.'),
 ('dash','The fix and this is important must not change words','The fix—and this is important—must not change words.'),
 ('quotes','She said do not deploy and left','She said, "Do not deploy," and left.'),
 ('colon','There is one problem the cache is stale','There is one problem: the cache is stale.'),
 ('ellipsis','We need logs traces metrics etc etc','We need logs, traces, metrics…'),
 ('list','We need three things first logs second traces third metrics','We need three things:\n- First, logs\n- Second, traces\n- Third, metrics'),
 ('paragraph','Hi Maya thanks for the update can you send the draft tomorrow best Alex','Hi Maya,\n\nThanks for the update. Can you send the draft tomorrow?\n\nBest,\nAlex'),
 ('number','Set the limit to twenty three and the threshold to ten percent','Set the limit to 23 and the threshold to 10%.'),
 ('number','The total is two hundred and fifty dollars','The total is $250.'),
 ('merge','super whispered should keep my words','Superwhisper should keep my words.'),
 ('clean',"I'm gonna keep this wording, not expand it.","I'm gonna keep this wording, not expand it."),
 ('filler','um uh',''),
]
for n,(cat,text,gold) in enumerate(new): rows.append(dict(id=f'n-{n+1}',category=cat,profile='prose',source='handwritten',private=False,text=text,expected=[gold]))
assert len(rows)==80
# Public manifest has no private text or private gold; private data excluded from git.
public=[{k:v for k,v in r.items() if not r['private'] or k not in ('text','expected')} for r in rows]
(P/'eval-manifest.json').write_text(json.dumps(public,indent=2,ensure_ascii=False)+'\n')
(P/'private-eval.json').write_text(json.dumps(rows,indent=2,ensure_ascii=False)+'\n'); (P/'private-eval.json').chmod(0o600)
(P/'dataset-info.json').write_text(json.dumps(dict(n=len(rows),snapshot_sha256=hashlib.sha256(pathlib.Path('/tmp/hark-history-snapshot.jsonl').read_bytes()).hexdigest()),indent=2))
