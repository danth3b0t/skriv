#!/usr/bin/env python3
"""Offline replay. No session text, vocabulary, repo paths, or per-target rows are saved.
Run with PYTHONDONTWRITEBYTECODE=1 and the project's existing .venv/bin/python.
All outputs are confined to this script's directory. --audit prints a private sample.
"""
from __future__ import annotations
import argparse
import importlib.abc
import importlib.util
import subprocess
import collections as C
import csv
import datetime as dt
import hashlib
import json
import math
import random
import re
import statistics
import sys
import time
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

OUT = Path(__file__).resolve().parent
PROJECT = OUT.parents[2]
sys.dont_write_bytecode = True
sys.path.insert(0, str(PROJECT / 'client/src'))
# Other agents are editing the working tree concurrently. Pin the context implementation
# matching the six files read at task start; load its sources in memory, never checkout.
BASELINE_REVISION = 'b71af4ad4a23dc9e0d42b0112d196f2114692830'
def git_object(*args):
    return subprocess.run(['git','--no-optional-locks','-C',str(PROJECT),*args],capture_output=True,check=True).stdout
BASELINE_SOURCES = {p:git_object('show',BASELINE_REVISION+':'+p).decode() for p in git_object('ls-tree','-r','--name-only',BASELINE_REVISION,'client/src/hark_client/context').decode().splitlines() if p.endswith('.py')}
class PinnedContext(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    def find_spec(self, fullname, path=None, target=None):
        if not fullname.startswith('hark_client.context'):return None
        rel='client/src/'+fullname.replace('.','/')
        for name,package in ((rel+'.py',False),(rel+'/__init__.py',True)):
            if name in BASELINE_SOURCES:
                spec=importlib.util.spec_from_loader(fullname,self,is_package=package)
                spec.loader_state=name
                return spec
        return None
    def create_module(self,spec):return None
    def exec_module(self,module):
        path=module.__spec__.loader_state
        module.__file__=str(PROJECT/path)
        exec(compile(BASELINE_SOURCES[path],f'git:{BASELINE_REVISION}:{path}','exec'),module.__dict__)
sys.meta_path.insert(0,PinnedContext())
from hark_client.context.aliases import extract_terms, spoken_forms, split_words
from hark_client.context.rank import Candidate, ranked, file_candidates
from hark_client.context.repo import RepoInfo, repo_candidates, _git, _identifiers
from hark_client.context.types import TermKind as TK, TermSource as TS
from hark_client.context.engine import scrub

KS = (10,20,40,60,100)
COUNTS = C.Counter()
DICT = {w.strip().lower() for w in Path('/usr/share/dict/words').read_text().splitlines()}
STOP = set('a an the i we you he she it they this that these those please yes no ok okay thanks thank hello hi use using used add added remove change fix run do done now then next first second third all any some more most less other only also just still already can could would should will shall may might must not and or but if else when while where what which who how why for from to in on at by with without into over under as of is are was were be been being have has had get got let lets want need make sure new old good great right left true false null none todo fixme note important summary task role rules output inputs context plan report review update implement work working user assistant system read write test tests build file files code project repo branch commit changes current previous following final required optional existing default example examples key value name path command result results failure success open close start stop set list check see find help continue return here there today tomorrow yesterday'.split())
# Independent gold patterns: do NOT call production extract_terms.
TOKEN = re.compile(r'(?<![\w])(?:--[A-Za-z][\w-]*|[~./]?[A-Za-z_][A-Za-z0-9_+.#/@:-]*[A-Za-z0-9_+#/]|[A-Za-z])(?![\w])')
EXT = set('py pyi ts tsx js jsx mjs cjs rs go md json jsonl toml yaml yml proto sh zsh fish lua nix c h cc cpp hpp java kt swift rb css scss html sql txt lock cfg ini env csv xml svg'.split())
TECH = set('pi uv tmux workmux hark codex claude openai anthropic parakeet qwen nemo mlx cuda pytorch typescript javascript bun nodejs pytest ruff numpy scipy sqlite postgres postgresql github gitlab docker kubernetes cmake protobuf grpc json yaml toml jsonl llm asr mcp tui cli api sdk sql http https url uuid iban swift react vite nextjs ollama ffmpeg quickjs xhigh'.split())

def english(t):
    low=t.lower()
    if low in STOP or low in DICT:return True
    for suffix in ('s','es','ed','ing','ly'):
        if low.endswith(suffix):
            stem=low[:-len(suffix)]
            if stem in DICT or stem+'e' in DICT or (len(stem)>2 and stem[-1:]==stem[-2:-1] and stem[:-1] in DICT):return True
    return low.endswith('ies') and low[:-3]+'y' in DICT

OPAQUE = re.compile(r'^(?:[a-fA-F0-9]{8,}|\d+(?:\.\d+)*|[A-Z0-9_-]{24,})$')
SCAFFOLD = re.compile(r'<(?:environment_context|permissions instructions|system-reminder|turn_aborted|subagent_notification)|^# (?:AGENTS\.md|Instructions)|^\[Request interrupted|^This session is being continued|^Shell command output follows\.', re.I)

def clean(text):
    text = re.sub(r'\b(?:sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|xox[abprs]-[A-Za-z0-9-]{10,}|AKIA[0-9A-Z]{16}|eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,})\b', ' ', text)
    text = re.sub(r'\b[A-Za-z]+[’\'][A-Za-z]+\b|</?[A-Za-z_][\w-]*[^>]*>', ' ', text)
    text = re.sub(r'(?:https?|file)://\S+|\b[\w.+-]+@[\w.-]+\.[a-z]{2,}\b', ' ', text)
    return re.sub(r'-{3,}', ' ', text.replace('[redacted]', ' '))

def valid(t):
    return 2 <= len(t) <= 100 and not OPAQUE.fullmatch(t) and not re.search(r'[a-fA-F0-9]{12,}|[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-',t) and not any(x in t.lower() for x in ('password=', 'token=', 'secret=', 'context-ranking'))

def classify(t, quoted=False):
    if t.startswith(('toolu_','call_')):return None
    if not valid(t) or t.lower() in STOP or re.fullmatch(r'[A-Za-z]\d+',t) or re.fullmatch(r'(?:u+g+h+|h+m+h*|ha(?:ha)+)',t,re.I): return None
    if t.startswith('--'): return TK.FLAG
    if (t.startswith(('/','~/','./')) or t.count('/')>=2 and any(not english(p) for p in t.split('/') if p) or '/' in t and quoted or t.rsplit('.',1)[-1] in EXT and '.' in t): return TK.PATH
    if '/' in t:return None
    if '_' in t or re.search(r'[a-z][A-Z]|[A-Z]{2}[a-z]|[a-zA-Z]\d|\d[a-zA-Z]',t): return TK.IDENTIFIER
    if t.isalpha() and t.isupper() and 2 <= len(t) <= 8 and not english(t): return TK.NAME
    if '-' in t or '.' in t:
        parts = re.split(r'[-.]',t)
        if quoted or any(not english(p) for p in parts): return TK.IDENTIFIER
        return None
    if t.isalpha() and (t.lower() in TECH or len(t)>=3 and not english(t) and (t[0].isupper() or quoted)):
        return TK.NAME
    return None

@lru_cache(maxsize=15000)
def gold(text):
    text = clean(text)
    out = {}
    quotes = [(m.start(1),m.end(1)) for m in re.finditer(r'`([^`\n]{1,100})`',text)]
    for m in TOKEN.finditer(text):
        t=m.group().strip('.,:;')
        k=classify(t,any(a <= m.start() < b for a,b in quotes))
        if k: out[t]=k
    # Multiword entity spans only, not arbitrary quoted shell commands/prose.
    for m in re.finditer(r'`([A-Za-z][A-Za-z0-9 +.#-]{2,60})`',text):
        t=m.group(1)
        if 2 <= len(t.split()) <= 4 and all(w[:1].isupper() for w in t.split()) and any(classify(w) for w in t.split()): out[t]=TK.NAME
    return out

@lru_cache(maxsize=18000)
def terms(text, expanded=False):
    text=clean(text)
    out={t:k for t,k in extract_terms(scrub(text),prose=True) if valid(t)}
    if expanded: out.update(gold(text))
    return tuple(out.items())

def text_blocks(content):
    if isinstance(content,str): return content
    if not isinstance(content,list): return ''
    return '\n'.join(b.get('text','') for b in content if isinstance(b,dict) and b.get('type') in ('text','input_text','output_text'))

def paths_from(obj):
    out=[]
    if isinstance(obj,dict):
        for k,v in obj.items():
            if k in ('path','file_path','notebook_path') and isinstance(v,str): out.append(v)
            elif k in ('arguments','input') and isinstance(v,str):
                try: out += paths_from(json.loads(v))
                except (ValueError,TypeError): pass
                out += re.findall(r'^\*\*\* (?:Add|Update|Delete) File: (.+)$',v,re.M)
            elif isinstance(v,(dict,list)): out += paths_from(v)
    elif isinstance(obj,list):
        for x in obj: out += paths_from(x)
    return list(dict.fromkeys(p for p in out if valid(p) and scrub(p)==p))

def stamp(v, fallback=0):
    try:
        if isinstance(v,(float,int)): return v/1000 if v>1e11 else float(v)
        return dt.datetime.fromisoformat(v.replace('Z','+00:00')).timestamp()
    except (ValueError,TypeError,AttributeError): return fallback

@dataclass
class Event:
    role: str
    text: str
    paths: list
    parent: str|None
    timestamp: float
    cwd: str
    branch: str=''
    target: bool=True

@dataclass
class Session:
    provider: str
    group: str
    nodes: dict=field(default_factory=dict)
    targets: list=field(default_factory=list)
    start: float=0
    end: float=0
    cwd: str=''
    split: str=''
    repo: str=''
    vocab: dict=field(default_factory=dict)

def load_sessions(cutoff):
    sessions=[]
    roots=[('pi','pi','~/.pi/agent/sessions'),('pi','pi_sub','~/.pi/agent/subagent-sessions'),('claude','claude','~/.claude/projects'),('codex','codex','~/.codex/sessions')]
    for provider,group,root in roots:
        for f in sorted(Path(root).expanduser().rglob('*.jsonl')):
            COUNTS['files_'+group]+=1
            s=Session(provider,group); last=None; skip=False
            # Snapshot file size avoids reading appended entries forever.
            size=f.stat().st_size
            with f.open('rb') as fh:
                while fh.tell()<size:
                    line=fh.readline()
                    try: e=json.loads(line)
                    except (ValueError,TypeError): COUNTS['malformed_lines']+=1; continue
                    typ=e.get('type'); p=e.get('payload',{})
                    ts=stamp(e.get('timestamp'))
                    if ts>cutoff: continue
                    if typ=='session' or typ=='session_meta':
                        meta=e if provider=='pi' else p
                        s.cwd=meta.get('cwd',s.cwd); s.start=stamp(meta.get('timestamp'),ts)
                        if provider=='codex' and (meta.get('parent_thread_id') or isinstance(meta.get('source'),dict)):
                            s.group='codex_sub'
                        continue
                    cwd=e.get('cwd',s.cwd)
                    if typ=='turn_context': s.cwd=p.get('cwd',s.cwd)
                    if cwd and not s.cwd: s.cwd=cwd
                    msg=e.get('message',{}) if provider!='codex' else p
                    role=msg.get('role','')
                    eid=e.get('id') if provider=='pi' else e.get('uuid')
                    parent=e.get('parentId') if provider=='pi' else e.get('parentUuid')
                    if provider=='codex':
                        if typ!='response_item': continue
                        eid=str(len(s.nodes)); parent=last
                        if p.get('type') in ('function_call','custom_tool_call'): role='assistant'
                    if not eid: continue
                    content=msg.get('content',[])
                    text=text_blocks(content)
                    paths=paths_from(content if provider!='codex' else p) if role=='assistant' else []
                    target=role=='user'
                    if provider=='claude':
                        if e.get('isSidechain'): s.group='claude_sub'
                        if isinstance(content,list) and any(b.get('type')=='tool_result' for b in content if isinstance(b,dict)): target=False
                    if target:
                        text=re.sub(r'<(system-reminder|environment_context|in-app-browser-context|external_codex_apps_open_page|subagent_notification)\b[^>]*>.*?</\1>', '', text, flags=re.S)
                        COUNTS['user_records']+=1
                        if 'Context-RANKING EXPERIMENTER' in text or ('context-ranking/' in text and 'ROLE:' in text): skip=True
                        if SCAFFOLD.search(text) or e.get('isMeta') or e.get('metadata',{}).get('inherited_user_message'):
                            target=False; COUNTS['scaffold_or_inherited']+=1
                        if not text.strip(): target=False
                    if role not in ('assistant','user'): text=''
                    # Tool results, thinking blocks, attachments never enter candidate text.
                    s.nodes[eid]=Event(role,clean(text),paths,parent,ts,cwd,e.get('gitBranch',''),target)
                    if target:s.targets.append(eid)
                    last=eid
                    if ts:
                        s.start=min(s.start or ts,ts);s.end=max(s.end,ts)
            if skip: COUNTS['experiment_sessions_excluded']+=1; continue
            if s.targets:
                sessions.append(s)
    sessions.sort(key=lambda s:s.start)
    seen={}
    for s in sessions:
        unique=[]
        for eid in s.targets:
            digest=hashlib.sha256(s.nodes[eid].text.encode()).digest()
            if digest in seen and seen[digest] is not s:
                COUNTS['cross_session_duplicate_targets']+=1; continue
            seen[digest]=s; unique.append(eid)
        s.targets=unique
        for eid in unique: s.vocab.update(gold(s.nodes[eid].text))
    sessions=[s for s in sessions if s.targets]
    # Split eligible-main sessions chronologically separately from auxiliary sessions;
    # a global split otherwise leaves NO short main-session prompts in the test set.
    for eligible in (True,False):
        cohort=[s for s in sessions if (('_sub' not in s.group) and any(len(s.nodes[eid].text)<=2000 and '```' not in s.nodes[eid].text for eid in s.targets)) == eligible]
        for i,s in enumerate(cohort):s.split='train' if i<len(cohort)*.6 else 'dev' if i<len(cohort)*.8 else 'test'
    return sessions

def history(s,eid):
    chain=[]; cur=s.nodes[eid].parent; seen=set()
    while cur and cur not in seen:
        seen.add(cur); e=s.nodes.get(cur)
        if e is None: COUNTS['missing_parent_edges']+=1; break
        chain.append(e); cur=e.parent
    # Response turns, not individual streaming blocks. Each block between human prompts
    # forms a single assistant turn; retain user-only turns as separate ages.
    turns=[]; texts=[]; paths=[]
    for e in chain:
        if e.role=='assistant':
            if e.text: texts.append(e.text)
            paths.extend(reversed(e.paths))
        elif e.target:
            turns.append(('\n\n'.join(reversed(texts)),list(dict.fromkeys(paths)),e.text))
            texts=[];paths=[]
        if len(turns)>=8:break
    if texts or paths:turns.append(('\n\n'.join(reversed(texts)),list(dict.fromkeys(paths)),''))
    return turns[:8]

REPOS={}
def repo_data(cwd):
    if cwd in REPOS:return REPOS[cwd]
    root=(_git(['rev-parse','--show-toplevel'],cwd) or '').strip()
    if not root: REPOS[cwd]=(None,[],[]); return REPOS[cwd]
    if root in REPOS:REPOS[cwd]=REPOS[root]; return REPOS[root]
    def allowed(p):return not p.startswith('docs/research/context-ranking/') and not any(x in p.split('/') for x in ('.env','.venv','node_modules','.git'))
    files=[p for p in (_git(['ls-files','-z'],root) or '').split('\0') if p and allowed(p)][:5000]
    changes=[]
    # Recent committed files plus tracked working modifications: excludes experiment outputs
    # and untracked private files; this deliberate safety deviation is reported.
    for p in (_git(['diff','--name-only','HEAD'],root) or '').splitlines():
        if allowed(p):changes.append(p)
    log=(_git(['log','-n10','--name-only','--format='],root) or '').splitlines()
    recent=list(dict.fromkeys(changes+[p for p in log if p and allowed(p)]))
    branch=(_git(['symbolic-ref','-q','--short','HEAD'],root) or '').strip()
    info=RepoInfo(root,branch,None,tuple(files),tuple(recent),_identifiers(root,recent))
    base=repo_candidates(info)
    whole=[]; read_files=0; total=0
    for p in sorted(files,key=lambda p:(p.count('/'),p)):
        whole.extend(file_candidates(p,TS.REPO,1/(1+p.count('/'))))
        whole.append(Candidate(p,TK.PATH,TS.REPO,.6))
        path=Path(root)/p
        if read_files>=200 or total>=10*1024*1024:continue
        try:
            if path.is_symlink() or path.stat().st_size>256*1024: continue
            data=path.read_bytes()
        except OSError:continue
        if b'\0' in data[:1024]:continue
        read_files+=1;total+=len(data)
        whole.extend(Candidate(t,k,TS.REPO,.5) for t,k in terms(data.decode(errors='replace')[:100000]) if k==TK.IDENTIFIER)
    COUNTS['repos_available']+=1;COUNTS['whole_repo_scanned_files']+=read_files
    # Deduplicate scan candidates before repeated scoring.
    whole=list({(c.canonical,c.source):c for c in whole if valid(c.canonical)}.values())
    REPOS[root]=REPOS[cwd]=(root,base,whole)
    return REPOS[cwd]

# English background: native system manual prose, not target sessions. Technical English
# is a biased but offline available background. Strip roff directives/escapes before DF.
BG_DF=C.Counter(); BG_N=0
for p in sorted(Path('/usr/share/man/man1').glob('*.1'))[:500]:
    try:t=p.read_text(errors='replace')
    except OSError:continue
    t='\n'.join(l for l in t.splitlines() if not l.startswith(('.',"'")))
    BG_DF.update(set(re.findall(r'[a-z]{2,}',t.lower())));BG_N+=1

def idf(t):
    words=[w.lower() for w in split_words(t) if len(w)>1]
    return sum(math.log((BG_N+1)/(BG_DF[w]+1))/math.log(BG_N+1) for w in words)/len(words) if words else 0

@dataclass(frozen=True)
class Strategy:
    name:str
    n:int=1
    expanded:bool=False
    tools:bool=False
    repo:str='none'
    user:bool=False
    persistent:str='none'
    formula:str='decay'
    decay:float=.7
    user_weight:float=1.1
    frequency:float=.15
    bonus:float=.12
    threshold:float=0

STRATEGIES=[Strategy('current_agent',formula='current'),Strategy('current_engine',tools=True,repo='current',formula='current')]
STRATEGIES += [Strategy(f'last{n}',n=n) for n in (1,2,4,8)]
STRATEGIES += [Strategy('expanded',n=4,expanded=True),Strategy('tools',n=4,expanded=True,tools=True)]
STRATEGIES += [Strategy(f'repo_{r}',n=4,expanded=True,tools=True,repo=r) for r in ('recent','whole','both')]
STRATEGIES += [Strategy('own_previous',n=4,expanded=True,tools=True,repo='recent',user=True)]
STRATEGIES += [Strategy(f'persist_{p}',n=4,expanded=True,tools=True,repo='recent',user=True,persistent=p) for p in ('repo','user','both')]
for formula in ('current','frequency','tfidf','no_bonus'):
    STRATEGIES.append(Strategy('rank_'+formula,n=4,expanded=True,tools=True,repo='recent',user=True,persistent='both',formula=formula))
for n in (2,4,8):
    for decay in (.4,.7,.9):
        STRATEGIES.append(Strategy(f'tune_n{n}_d{decay}',n=n,expanded=True,tools=True,repo='recent',user=True,persistent='both',decay=decay))
for uw in (.7,1.5):
    STRATEGIES.append(Strategy(f'tune_user{uw}',n=4,expanded=True,tools=True,repo='recent',user=True,persistent='both',user_weight=uw))
for threshold in (.3,.5,.7):
    STRATEGIES.append(Strategy(f'gate{threshold}',n=4,expanded=True,tools=True,repo='recent',user=True,persistent='both',threshold=threshold))
# Strictly time-respecting / no-repo-state ablations.
STRATEGIES += [Strategy('temporal_only',n=4,expanded=True,tools=True,user=True,persistent='both'), Strategy('history_no_persist',n=4,expanded=True,tools=True,user=True)]

# Post-selection source ablations: not eligible for model selection.
for name,kw in [('selected_no_repo',dict(repo='none')),('selected_no_persist',dict(persistent='none')),('selected_no_user',dict(user=False)),('selected_no_tools',dict(tools=False)),('selected_repo_persist',dict(persistent='repo'))]:
    opts=dict(n=8,expanded=True,tools=True,repo='recent',user=True,persistent='both',decay=.9);opts.update(kw)
    STRATEGIES.append(Strategy(name,**opts))

SOURCE_W={'assistant':.85,'tools':.75,'repo_recent':.45,'repo':.25,'user':1.1,'persist_repo':.55,'persist_user':.3,'cwd':.45}
SOURCE_MAP={'assistant':TS.AGENT_SESSION,'tools':TS.AGENT_SESSION,'user':TS.LEARNED,'repo_recent':TS.REPO_RECENT,'repo':TS.REPO,'persist_repo':TS.VOCABULARY,'persist_user':TS.VOCABULARY,'cwd':TS.REPO_RECENT}

@lru_cache(maxsize=100000)
def aliases(t,k):
    try:return spoken_forms(t,k)
    except (ValueError,IndexError,OverflowError):return ()

def candidates(h,base,whole,persist,cwd,branch):
    out=[]
    # (canonical, kind, source, age, within-source score, expanded-only)
    for age,(a,ps,u) in enumerate(h):
        original=dict(terms(a[-1500:])); expanded=dict(terms(a[-1500:],True))
        for t,k in {**expanded,**original}.items():out.append((t,k,'assistant',age,1,t not in original))
        for p in ps[:30]:
            for t,k in [(p,TK.PATH),(Path(p).name,TK.PATH),(Path(p).name.split('.')[0],TK.IDENTIFIER)]:
                if valid(t):out.append((t,k,'tools',age,1,False))
        for t,k in terms(u[-6000:],True):out.append((t,k,'user',age,1,False))
    for c in base:out.append((c.canonical,c.kind,'repo_recent' if c.source==TS.REPO_RECENT else 'repo',0,c.score,False))
    for c in whole:out.append((c.canonical,c.kind,'whole',0,c.score,False))
    for t,k,source,score in persist:out.append((t,k,source,0,score,False))
    for t,k in gold(Path(cwd).name+' '+branch).items():out.append((t,k,'cwd',0,1,False))
    return out

def shortlist(st,cs,h,base):
    if st.name in ('current_agent','current_engine'):
        c=[]
        if h:
            reply=''; ps=[]
            for text,paths,_ in h:
                ps.extend(paths)
                if text.strip():reply=text;break
            ps=list(dict.fromkeys(ps))
            c=[Candidate(t,k,TS.AGENT_SESSION) for t,k in terms(reply[-1500:])]
            if st.tools and reply:
                n=max(1,len(ps))
                for i,p in enumerate(ps[:30]):c.extend(file_candidates(p,TS.AGENT_SESSION,1-i/n))
        if st.repo=='current':c+=base
        return [(x.canonical,x.weight,{ 'assistant' if x.source==TS.AGENT_SESSION else 'repo_recent' if x.source==TS.REPO_RECENT else 'repo'}) for x in ranked(c,100)]
    merged={}
    for t,k,src,age,score,extra in cs:
        if not valid(t) or age>=st.n:continue
        if src=='assistant' and extra and not st.expanded:continue
        if src=='tools' and not st.tools:continue
        if src=='user' and not st.user:continue
        if src=='cwd' and st.repo=='none':continue
        if src=='repo_recent' and st.repo not in ('recent','both','current'):continue
        if src=='repo' and st.repo!='current':continue
        if src=='whole':
            if st.repo not in ('whole','both'):continue
            src='repo'
        if src=='persist_repo' and st.persistent not in ('repo','both'):continue
        if src=='persist_user' and st.persistent not in ('user','both'):continue
        w=st.user_weight if src=='user' else SOURCE_W[src]
        v=w*st.decay**age*score
        if st.formula=='current':
            from hark_client.context.rank import SOURCE_WEIGHT
            v=SOURCE_WEIGHT[SOURCE_MAP[src]]*(.6+.4*score*st.decay**age)
        item=merged.setdefault(t,[0,set(),set(),k])
        item[0]=max(item[0],v);item[1].add(src);item[2].add((src,age))
    result=[]
    for t,(score,sources,occurrences,k) in merged.items():
        freq=sum(st.decay**age for _,age in occurrences)
        if st.formula=='current':score=min(1,score+.05*(len(sources)-1))
        elif st.formula=='frequency':score*=1+.5*math.log1p(freq-1)
        else:
            score*=1+st.frequency*math.log1p(max(0,freq-1))
            if st.formula!='no_bonus':score+=st.bonus*(len(sources)-1)
            if st.formula=='tfidf':score*=.5+.5*idf(t)
        if score>=st.threshold and aliases(t,k):result.append((t,score,sources))
    return sorted(result,key=lambda x:(-x[1],x[0]))[:100]

class Metric:
    def __init__(self):self.c=C.Counter()
    def add(self,g,short,present_terms,oracle):
        c=self.c;c['targets']+=1;c['gold']+=len(g);c['terms']+=len(short)
        selected={x[0] for x in short};hits=len(selected&g)
        # Boundary-aware case-insensitive lexical presence; not restricted to gold labels.
        present=len(selected & present_terms)
        c['hits']+=hits;c['present']+=present;c['distractors']+=len(short)-present
        if g:c['gold_targets']+=1;c['macro_recall_sum']+=hits/len(g);c['oracle_sum']+=len(g&oracle)/len(g)
        if short:c['nonempty']+=1;c['precision_sum']+=present/len(short)
    def row(self):
        c=self.c;n=c['targets'];ng=c['gold_targets']
        return dict(targets=n,gold_targets=ng,gold=c['gold'],micro_recall=c['hits']/c['gold'] if c['gold'] else 0,macro_recall=c['macro_recall_sum']/ng if ng else 0,precision=c['present']/c['terms'] if c['terms'] else 0,macro_precision_nonempty=c['precision_sum']/c['nonempty'] if c['nonempty'] else 0,mean_terms=c['terms']/n,mean_distractors=c['distractors']/n,oracle_macro_recall=c['oracle_sum']/ng if ng else 0)

def save_csv(name,rows):
    if not rows:return
    with (OUT/name).open('w') as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--audit',action='store_true');args=ap.parse_args()
    cutoff_path=OUT/'cutoff.json'
    if cutoff_path.exists():cutoff=json.loads(cutoff_path.read_text())['unix_seconds']
    else:
        cutoff=time.time();cutoff_path.write_text(json.dumps({'unix_seconds':cutoff})+'\n')
    sessions=load_sessions(cutoff)
    print('Loaded',len(sessions),'sessions;',sum(len(s.targets) for s in sessions),'targets',flush=True)
    if args.audit:
        rng=random.Random(719); pool=[]; empties=[]
        for s in sessions:
            if '_sub' in s.group:continue
            for eid in s.targets:
                text=s.nodes[eid].text
                if len(text)>2000:continue
                for t,k in gold(text).items():pool.append((t,k.value))
                if not gold(text):empties.append(text)
        print('PRIVATE gold review (not written):',json.dumps(list(enumerate(rng.sample(pool,min(80,len(pool))),1))))
        print('PRIVATE missed-term review (not written):',json.dumps([t[:220] for t in rng.sample(empties,min(12,len(empties)))]))
        return
    for s in sessions:s.repo=repo_data(s.cwd)[0] or s.cwd
    metrics={};providers={};strict_metrics={};strata={};split_stats=C.Counter();sc=C.Counter(); aliasstats=C.Counter();kindstats=C.Counter();coverage=C.Counter();boot={};allgold={}
    population=C.Counter(); repo_vocab=C.defaultdict(dict);user_vocab={}
    pending=sorted(enumerate(sessions),key=lambda pair:pair[1].end); pi=0
    for si,s in enumerate(sessions):
        # Strong time rule: complete earlier session end < current session start.
        while pi<len(pending) and pending[pi][1].end<s.start:
            _,old=pending[pi];pi+=1
            if old is s:continue
            # Only human main sessions can teach persistent vocabulary, never generated tasks.
            if '_sub' in old.group:continue
            for t,k in old.vocab.items():
                for vocab in (user_vocab,repo_vocab[old.repo]):
                    prev=vocab.get(t,(k,0,0));vocab[t]=(k,prev[1]+1,max(prev[2],old.end))
        persist=[]
        for src,vocab in [('persist_repo',repo_vocab[s.repo]),('persist_user',user_vocab)]:
            pool=[]
            for t,(k,freq,last) in vocab.items():
                age=max(0,(s.start-last)/86400)
                score=(.5+.5*min(1,math.log1p(freq)/math.log(6)))*2**(-age/30)
                pool.append((t,k,src,score))
            persist.extend(sorted(pool,key=lambda x:(-x[3],x[0]))[:300])
        _,base,whole=repo_data(s.cwd)
        for eid in s.targets:
            e=s.nodes[eid];gdict=gold(e.text);g=set(gdict);h=history(s,eid)
            primary='_sub' not in s.group and len(e.text)<=2000 and '```' not in e.text
            cohort='primary' if primary else 'subagent' if '_sub' in s.group else 'long_or_code'
            population[(s.split,cohort,s.group)]+=1
            if primary:
                split_stats[(s.split,'targets')]+=1
                split_stats[(s.split,'with_repo')]+=bool(base)
                split_stats[(s.split,'no_history')]+=not bool(h)
                split_stats[(s.split,'gold_targets')]+=bool(g)
                allgold.update(gdict)
                for t,k in gdict.items():kindstats[(s.split,k.value)]+=1
            cs=candidates(h,base,whole,persist,e.cwd,e.branch)
            lower=e.text.lower()
            present_terms={t for t,*_ in cs if t.lower() in lower and re.search(r'(?<![\w])'+re.escape(t)+r'(?![\w])',e.text,re.I)}
            strict={t for t,k in gdict.items() if k!=TK.NAME}
            for st in STRATEGIES:
                sh=shortlist(st,cs,h,base)
                # Candidate-pool ceiling uses same filters by re-ranking with enlarged K?
                # Coverage below independently measures all input-source terms (broader ceiling).
                oracle={x[0] for x in cs}
                for k in KS:
                    key=(s.split,cohort,st.name,k)
                    metrics.setdefault(key,Metric()).add(g,sh[:k],present_terms,oracle)
                if primary:
                    for label in ('with_history' if h else 'no_history','gold' if g else 'zero_gold','repo_available' if base else 'repo_missing'):
                        strata.setdefault((s.split,label,st.name),Metric()).add(g,sh[:40],present_terms,oracle)
                    providers.setdefault((s.split,s.group,st.name),Metric()).add(g,sh[:40],present_terms,oracle)
                    strict_metrics.setdefault((s.split,st.name),Metric()).add(strict,sh[:40],present_terms,oracle)
                    for k in KS:
                        b=boot.setdefault((s.split,si,st.name,k),[0,0,0,0])
                        if g:b[0]+=len(g&{x[0] for x in sh[:k]})/len(g);b[1]+=1
                        b[2]+=len(sh[:k]);b[3]+=1
                    if s.split=='test':
                        # Fractional attribution prevents double counting overlaps.
                        for t,score,sources in sh[:40]:
                            for src in sources:
                                sc[(st.name,src,'selected')]+=1/len(sources)
                                if t in g:sc[(st.name,src,'gold_hits')]+=1/len(sources)
                if primary and st.name=='persist_both':
                    groups=C.defaultdict(set)
                    for t,_,source,*_ in cs:groups[source].add(t)
                    for src,ts in groups.items():
                        coverage[(s.split,src,'hits')]+=len(g&ts)
                        coverage[(s.split,src,'exclusive')]+=len((g&ts)-set().union(*(v for key,v in groups.items() if key!=src)))
                    coverage[(s.split,'denominator','gold')]+=len(g)
        if si%50==0:print('Replayed',si,'/',len(sessions),flush=True)
    rows=[dict(split=sp,cohort=co,strategy=st,k=k,**m.row()) for (sp,co,st,k),m in metrics.items()]
    save_csv('metrics.csv',rows)
    save_csv('strata.csv',[dict(split=sp,stratum=label,strategy=st,k=40,**m.row()) for (sp,label,st),m in strata.items()])
    save_csv('provider_metrics.csv',[dict(split=sp,provider=pr,strategy=st,k=40,**m.row()) for (sp,pr,st),m in providers.items()])
    save_csv('strict_gold_metrics.csv',[dict(split=sp,strategy=st,k=40,**m.row()) for (sp,st),m in strict_metrics.items()])
    save_csv('population.csv',[dict(split=sp,cohort=co,provider=pr,targets=n) for (sp,co,pr),n in population.items()])
    save_csv('source_attribution.csv',[dict(strategy=st,source=src,metric=m,value=v) for (st,src,m),v in sc.items()])
    save_csv('source_coverage.csv',[dict(split=sp,source=src,metric=m,value=v) for (sp,src,m),v in coverage.items()])
    save_csv('gold_kinds.csv',[dict(split=sp,kind=k,occurrences=n) for (sp,k),n in kindstats.items()])
    # Independent alias heuristic: preserve all alphanumeric chunks in order (acronyms
    # can be letter-spaced), alphabetic-only form, <=24 spoken words; punctuation checks.
    collisions=C.defaultdict(set)
    for t,k in allgold.items():
        als=aliases(t,k);prefix=k.value
        aliasstats[(prefix,'unique_gold')]+=1
        if als:aliasstats[(prefix,'nonempty')]+=1
        natural=[]
        chunks=re.findall(r'[a-z]+|\d+',t.lower())
        for a in als:
            compact=a.replace(' ','')
            number_words='zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen twenty'.split()
            preserved=all(chunk in compact or chunk.isdigit() and int(chunk)<len(number_words) and number_words[int(chunk)] in a.split() for chunk in chunks)
            alphabetic=bool(re.fullmatch('[a-z ]+',a))
            punctuation=not (t.startswith('--') and 'dash' not in a) and not ('.' in t and k==TK.PATH and 'dot' not in a)
            if preserved and alphabetic and punctuation and len(a.split())<=24:natural.append(a)
            collisions[a].add(t)
        if natural:aliasstats[(prefix,'heuristic_plausible')]+=1
        if any(re.search(r'\d',a) for a in als):aliasstats[(prefix,'has_digit_alias')]+=1
    save_csv('aliases.csv',[dict(kind=k,metric=m,value=n) for (k,m),n in aliasstats.items()])
    # Selection is DEV only. Max recall subject to actual mean list <= 40; choose smallest
    # K within one percentage point of the best development recall under that budget.
    dev=[r for r in rows if r['split']=='dev' and r['cohort']=='primary' and r['mean_terms']<=40 and not r['strategy'].startswith('selected_')]
    best=max(dev,key=lambda r:r['macro_recall'])
    near=[r for r in dev if r['macro_recall']>=best['macro_recall']-.01]
    chosen=min(near,key=lambda r:(r['mean_distractors'],-r['macro_recall'],r['k']))
    # Session-cluster paired bootstrap on held-out targets. 2k deterministic resamples.
    ci=[];rng=random.Random(719)
    for comparator in ('current_agent','current_engine','history_no_persist','persist_repo','rank_tfidf'):
        k=chosen['k'];ids=sorted({key[1] for key in boot if key[0]=='test'})
        diffs=[]
        for _ in range(2000):
            sums=[0,0,0,0]
            for sid in rng.choices(ids,k=len(ids)):
                a=boot.get(('test',sid,chosen['strategy'],k),[0,0]);b=boot.get(('test',sid,comparator,k),[0,0])
                sums=[sums[0]+a[0],sums[1]+a[1],sums[2]+b[0],sums[3]+b[1]]
            diffs.append(sums[0]/max(1,sums[1])-sums[2]/max(1,sums[3]))
        diffs.sort();ci.append(dict(comparator=comparator,k=k,delta_mean=statistics.mean(diffs),low95=diffs[50],high95=diffs[1949]))
    save_csv('bootstrap.csv',ci)
    save_csv('split_stats.csv',[dict(split=sp,metric=key,value=n) for (sp,key),n in split_stats.items()])
    metadata=dict(counters=dict(COUNTS),sessions=len(sessions),session_splits=dict(C.Counter(s.split for s in sessions)),primary_session_splits=dict(C.Counter(s.split for s in sessions if '_sub' not in s.group and any(len(s.nodes[eid].text)<=2000 and '```' not in s.nodes[eid].text for eid in s.targets))),baseline_revision=BASELINE_REVISION,implementation_sha256={name:hashlib.sha256(BASELINE_SOURCES['client/src/hark_client/context/'+name].encode()).hexdigest() for name in ('aliases.py','rank.py','agents.py','repo.py','canon.py','engine.py')},python_version=sys.version.split()[0],background_documents=BG_N,background_unique_words=len(BG_DF),dictionary_words=len(DICT),alias_collision_forms=sum(len(v)>1 for v in collisions.values()),alias_total_forms=len(collisions),strategies=[vars(st) for st in STRATEGIES],selection=chosen,cutoff_unix=cutoff)
    (OUT/'summary.json').write_text(json.dumps(metadata,indent=2)+'\n')
    print('DONE. Selected on development:',chosen,flush=True)

if __name__=='__main__':main()
