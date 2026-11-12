"""Recompute all aggregates from per-case public metrics; strict pipeline replay."""
import collections,json,pathlib
from run import P,score,quantile
from checker import insertion_only
cases={c['id']:c for c in json.loads((P/'private-eval.json').read_text())}
base={c['id']:c for c in json.loads((P/'private-s1-semi-formal-prose-general.json').read_text())}
for path in P.glob('pipeline-*-results.json'):
 name=path.name.removesuffix('-results.json');raw=json.loads(path.read_text());outputs={c['id']:c for c in json.loads((P/('private-'+name+'.json')).read_text())}
 for r in raw:
  c=cases[r['id']];o=outputs[r['id']]['output'];b=base[r['id']]['repaired'];ok=insertion_only(b,o)
  r['repaired']=score(c,o if ok else b);r['strict_rejected']=not ok
 (P/('strict-'+path.name)).write_text(json.dumps(raw,indent=2,ensure_ascii=False))

def aggregate(rs):
 d=dict(n=len(rs),exact_raw=sum(r['raw']['exact'] for r in rs),exact=sum(r['repaired']['exact'] for r in rs),invalid=sum(r['invalid'] for r in rs),audit_raw=sum(r['raw']['audit_violation'] for r in rs),audit_repaired=sum(r['repaired']['audit_violation'] for r in rs),lexical_exact=sum(r['repaired']['lexical_exact'] for r in rs),p50=round(quantile([r['elapsed_ms'] for r in rs],.5),1),p95=round(quantile([r['elapsed_ms'] for r in rs],.95),1),max_ms=round(max(r['elapsed_ms'] for r in rs),1),errors=sum(r['finish'] in ('length','error') for r in rs))
 corr=[r for r in rs if r['category']=='self_correction'];d['correction']=[sum(r['repaired']['lexical_exact'] for r in corr),len(corr)]
 d['fillers']=[sum(r['repaired']['fillers'][i] for r in rs) for i in range(2)]
 pm=collections.defaultdict(lambda:[0,0,0])
 for r in rs:
  for mark,counts in r['repaired']['punct'].items():
   for i,n in enumerate(counts):pm[mark][i]+=n
 d['punctuation']={m:dict(tp=x[0],pred=x[1],gold=x[2],precision=round(x[0]/x[1],3) if x[1] else None,recall=round(x[0]/x[2],3) if x[2] else None) for m,x in pm.items()}
 d['strict_rejected']=sum(r.get('strict_rejected',False) for r in rs)
 return d
summary={}
for p in sorted(P.glob('*-results.json')):
 rs=json.loads(p.read_text());s=aggregate(rs)
 s['sources']={src:aggregate([r for r in rs if r['source']==src]) for src in set(r['source'] for r in rs)}
 s['lengths']={tag:aggregate(sub) for tag,sub in [('<=20w',[r for r in rs if r['words']<=20]),('>20w',[r for r in rs if r['words']>20])] if sub}
 summary[p.name.removesuffix('-results.json')]=s
(P/'summary.json').write_text(json.dumps(summary,indent=2,ensure_ascii=False)+'\n')
lines=['| Configuration | Exact raw → repaired | Runtime violations | Gold-audited violations raw → repaired | Corrections | Fillers | p50 / p95 ms |','|---|---:|---:|---:|---:|---:|---:|']
for name,s in summary.items():
 lines.append(f"| {name} | {s['exact_raw']} → {s['exact']} / {s['n']} | {s['invalid']} | {s['audit_raw']} → {s['audit_repaired']} | {'/'.join(map(str,s['correction']))} | {'/'.join(map(str,s['fillers']))} | {s['p50']} / {s['p95']} |")
(P/'TABLES.md').write_text('\n'.join(lines)+'\n')
print('\n'.join(lines))
