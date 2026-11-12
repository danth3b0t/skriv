"""Latency-only stress test: full deduplicated private snapshot, no quality claims."""
import json,time
from run import P,server,request,quantile,repair,tokens
history=[json.loads(l)['raw_text'] for l in open('/tmp/hark-history-snapshot.jsonl')]
texts=list(dict.fromkeys(t for t in history if t.strip()))
proc,log=server('s1-mini-q4_k_m');rows=[]
try:
 request(dict(text='um please check this',profile='prose'),('semi-formal','prose','general'))
 for repeat in range(3):
  for i,text in enumerate(texts):
   c=dict(text=text,profile='prose');t=time.perf_counter()
   out,ms,finish,_=request(c,('semi-formal','prose','general'));repair(text,out)
   rows.append(dict(index=i,repeat=repeat,words=len(tokens(text)),ms=ms,total_ms=(time.perf_counter()-t)*1000,finish=finish))
finally:proc.terminate();proc.wait(timeout=30);log.close()
(P/'history-latency.json').write_text(json.dumps(rows,indent=2))
print('unique',len(texts),'n',len(rows),'p50',quantile([r['total_ms'] for r in rows],.5),'p95',quantile([r['total_ms'] for r in rows],.95),'max',max(r['total_ms'] for r in rows))
