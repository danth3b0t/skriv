"""Research checker, not a semantic proof. No gold labels used in repair.
Tokens retain contractions, numeric symbols, and code-like internal punctuation.
ITN canonicalization covers integers <=999, ordinals <=20, percent, dollars.
"""
import re
from difflib import SequenceMatcher
from dataclasses import dataclass
TOKEN=re.compile(r"\$?\d[\d,]*(?:\.\d+)*(?:st|nd|rd|th|%)?|[^\W\d][\w]*(?:['’./-][\w]+)*",re.UNICODE)
UNITS=dict(zip('zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen'.split(),range(20)))
TENS=dict(zip('twenty thirty forty fifty sixty seventy eighty ninety'.split(),range(20,100,10)))
ORD=dict(zip('first second third fourth fifth sixth seventh eighth ninth tenth eleventh twelfth thirteenth fourteenth fifteenth sixteenth seventeenth eighteenth nineteenth twentieth'.split(),range(1,21)))
FILL={'um','uh','umm','uhh','er','erm','uhm','hmm'}
CUES=[['no','wait'],['wait','no'],['sorry','i','mean'],['i','mean'],['scratch','that'],['or','rather'],['uh','no'],['uh','no','uh']]
MERGES={('super','whispered'):'superwhisper',('super','whisper'):'superwhisper'}
@dataclass
class Token:
 key:str
 start:int
 end:int

def tokens(text,canon=True):
 ts=[Token(m.group().casefold().replace('’',"'"),m.start(),m.end()) for m in TOKEN.finditer(text)]
 if not canon:return ts
 out=[];i=0
 while i<len(ts):
  w=ts[i].key;j=i+1;k=w
  if tuple(t.key for t in ts[i:i+2]) in MERGES:
   k=MERGES[tuple(t.key for t in ts[i:i+2])];j=i+2
  elif w in UNITS or w in TENS:
   n=UNITS.get(w,TENS.get(w));j=i+1
   if j<len(ts) and ts[j].key=='hundred' and 0<n<10:
    n*=100;j+=1
    if j<len(ts) and ts[j].key=='and':j+=1
    if j<len(ts) and ts[j].key in TENS:n+=TENS[ts[j].key];j+=1
    if j<len(ts) and ts[j].key in UNITS:n+=UNITS[ts[j].key];j+=1
   elif w in TENS and j<len(ts) and ts[j].key in UNITS and UNITS[ts[j].key]<10:
    n+=UNITS[ts[j].key];j+=1
   k=str(n)
   if j<len(ts) and ts[j].key=='percent': k+='%';j+=1
   elif j<len(ts) and ts[j].key in ('dollar','dollars'):k='$'+k;j+=1
  elif w in ORD:
   n=ORD[w];k=str(n)+('th' if 10<n<14 else {1:'st',2:'nd',3:'rd'}.get(n%10,'th'))
  elif w[0].isdigit() or w[0]=='$': k=w.replace(',','')
  out.append(Token(k,ts[i].start,ts[j-1].end));i=j
 return out

def keys(text): return [t.key for t in tokens(text)]

def deletion_ok(a,i,j):
 ws=[t.key for t in a]; cut=ws[i:j]
 if not cut:return True
 # Cue must end the deleted span and replacement must remain after it.
 for cue in CUES:
  for suffix in range(min(4,i,len(cut))+1):
   core=cut[:-suffix] if suffix else cut
   if suffix and cut[-suffix:]!=ws[i-suffix:i]:continue
   if len(core)>len(cue) and core[-len(cue):]==cue and j<len(a) and len(core)-len(cue)<=12:
    # A bounded syntactic approximation, not proof of intended retraction.
    return True
 p=i
 while p<j:
  w=ws[p]
  if w in FILL:p+=1
  elif (p+1<len(ws) and w==ws[p+1]) or (p>0 and w==ws[p-1]):p+=1
  elif ws[p:p+2] in [['you','know'],['kind','of'],['sort','of']] and p+2<=j:p+=2
  elif w=='like' and p>0 and ws[p-1] in ('to','and','the','shall','should','could',"shouldn't",'in'):p+=1
  elif w in ('basically','literally') and (p==0 or ws[p-1] in FILL|{'so'}):p+=1
  elif ws[p:p+2]==['etc','etc'] and p+2<=j:p+=2 # explicit ellipsis convention
  else:return False
 return True

def invalid_blocks(source,out,punctuation_only=False):
 a=tokens(source,not punctuation_only);b=tokens(out,not punctuation_only)
 if punctuation_only:
  # Exact original lexical spellings/casing, not merely case-folded identity.
  ak=[source[t.start:t.end] for t in a];bk=[out[t.start:t.end] for t in b]
 else: ak=[t.key for t in a];bk=[t.key for t in b]
 bad=[]
 for tag,i,j,k,l in SequenceMatcher(None,ak,bk,autojunk=False).get_opcodes():
  if tag=='equal':continue
  if not punctuation_only and tag=='delete' and deletion_ok(a,i,j):continue
  bad.append((tag,i,j,k,l))
 return bad

def repair(source,out,punctuation_only=False):
 bad=invalid_blocks(source,out,punctuation_only)
 if not bad:return out,0
 a=tokens(source,not punctuation_only);b=tokens(out,not punctuation_only)
 # Fall back only lexical mismatch spans; preserve punctuation proposals elsewhere.
 for tag,i,j,k,l in reversed(bad):
  s=b[k].start if k<len(b) else len(out);e=b[l-1].end if l>k else s
  replacement=source[a[i].start:a[j-1].end] if j>i else ''
  if s>0 and replacement and not out[s-1].isspace():replacement=' '+replacement
  if e<len(out) and replacement and out[e].isalnum():replacement+=' '
  out=out[:s]+replacement+out[e:]
 if invalid_blocks(source,out,punctuation_only):return source,len(bad)
 return out.strip(),len(bad)

def gold_lexical_ok(source,out,golds):
 """Label-aware audit: require every gold-required token; retain any raw tokens.
 This catches arbitrary deletions the runtime heuristic allows. Not used in repair.
 """
 a=keys(source);b=keys(out)
 for gold in golds:
  g=keys(gold)
  # Output must contain all gold words in order, with only source words as extras.
  sm=SequenceMatcher(None,a,b,autojunk=False)
  if any(tag in ('insert','replace') for tag,*_ in sm.get_opcodes()):continue
  it=iter(b)
  if all(any(x==w for x in it) for w in g):return True
 return False

def punctuation(text):
 """Marks anchored at lexical gaps, rather than unlocated mark counts."""
 ts=tokens(text); events=[];last=0
 for i,t in enumerate(ts+[Token('',len(text),len(text))]):
  gap=text[last:t.start]
  gap=gap.replace('...','…').replace('“','"').replace('”','"')
  for mark in re.findall(r'\n\s*\n|[.,;:?!()"—…]|^\s*[-*•] ',gap,re.M):
   kind='paragraph' if '\n' in mark and mark.strip()=='' else 'bullet' if mark.strip() in ('-','*','•') else mark
   events.append((i,kind))
  last=t.end
 return events


def insertion_only(source, output):
 """True only if source bytes (Unicode characters) survive in order.
 Added characters must be whitespace or punctuation, never letters/digits.
 """
 import unicodedata
 i=0
 for ch in output:
  if i<len(source) and ch==source[i]:i+=1
  elif not (ch.isspace() or unicodedata.category(ch).startswith('P')):return False
 return i==len(source)
