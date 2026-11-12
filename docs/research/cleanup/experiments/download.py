"""Pinned HF downloads, sha256 verified against Hub LFS metadata. Stdlib only."""
import concurrent.futures, hashlib, json, pathlib, urllib.request, subprocess
ROOT = pathlib.Path(__file__).resolve().parents[4]
OUT = pathlib.Path(__file__).parent
REPOS = {
 'qwen3-4b': 'unsloth/Qwen3-4B-GGUF',
 'qwen3.5-0.8b': 'unsloth/Qwen3.5-0.8B-GGUF',
 'qwen3.5-2b': 'unsloth/Qwen3.5-2B-GGUF',
 'gemma3-4b': 'unsloth/gemma-3-4b-it-GGUF',
 'phi4-mini': 'unsloth/Phi-4-mini-instruct-GGUF',
}
def download(item):
 key, repo = item
 meta = json.load(urllib.request.urlopen(f'https://huggingface.co/api/models/{repo}?blobs=true'))
 (OUT / (key+'-hub.json')).write_text(json.dumps(meta, indent=2))
 files = [s for s in meta['siblings'] if s['rfilename'].lower().endswith('q4_k_m.gguf') and 'mmproj' not in s['rfilename']]
 if len(files)!=1: raise ValueError(files)
 f=files[0]; url=f"https://huggingface.co/{repo}/resolve/{meta['sha']}/{f['rfilename']}"
 path=ROOT/'models'/(key+'-q4_k_m.gguf')
 if not path.exists():
  subprocess.run(['curl','-fL','--retry','3','-sS','-o',str(path)+'.part',url],check=True)
  pathlib.Path(str(path)+'.part').rename(path)
 sha=hashlib.file_digest(path.open('rb'),'sha256').hexdigest()
 assert sha==f['lfs']['sha256'], (key,sha,f['lfs'])
 record=dict(key=key+'-q4_k_m',repo=repo,revision=meta['sha'],url=url,sha256=sha,bytes=path.stat().st_size)
 (OUT/(key+'-download.json')).write_text(json.dumps(record,indent=2)+'\n')
 print(key,'verified',flush=True)
 return record
if __name__=='__main__':
 with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
  records=list(pool.map(download, REPOS.items()))
 (OUT/'downloads.json').write_text(json.dumps(records,indent=2)+'\n')
