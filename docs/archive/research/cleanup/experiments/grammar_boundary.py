"""Second grammar: require whitespace boundaries to prohibit concatenation.
Still allows arbitrary deletion, so postvalidation is required.
"""
import json
import run
from checker import tokens
from run import P,server,evaluate

def bounded(text):
 words=[text[t.start:t.end] for t in tokens(text,False)]
 rules=['root ::= marks s0',r'marks ::= [.,;:?!()"—…]*',r'sep ::= marks [ \t\n]+ marks']
 for i,w in enumerate(words):rules.append(f's{i} ::= ({json.dumps(w,ensure_ascii=False)} sep)? s{i+1}')
 rules.append(f's{len(words)} ::= ""');return '\n'.join(rules)
run.grammar_for=bounded
cases=[c for c in json.loads((P/'private-eval.json').read_text()) if c['source']=='handwritten']
proc,log=server('qwen3-1.7b-q4_k_m')
try:evaluate('grammar-boundary-qwen3-1.7b-q4_k_m',cases,grammar=True)
finally:proc.terminate();proc.wait(timeout=30);log.close()
