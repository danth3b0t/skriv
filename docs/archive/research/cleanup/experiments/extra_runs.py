import json
from run import P,server,evaluate
cases=json.loads((P/'private-eval.json').read_text())
for model in ['qwen3-4b-q8_0','qwen3.5-0.8b-q8_0','qwen3.5-2b-q8_0','gemma3-4b-q8_0','phi4-mini-q8_0']:
 proc=log=None
 try:
  proc,log=server(model)
  evaluate(model,cases)
 finally:
  if proc:proc.terminate();proc.wait(timeout=30)
  if log:log.close()
# Repeated warm measurements of the front-runner (same 80 inputs, three passes).
proc,log=server('s1-mini-q4_k_m')
try:
 for i in range(1,4):evaluate(f'repeat-{i}-s1',cases,('semi-formal','prose','general'))
finally:proc.terminate();proc.wait(timeout=30);log.close()
