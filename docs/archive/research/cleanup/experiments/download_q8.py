"""Additional quantization sweep; pinned URLs + checked LFS sha256."""
import concurrent.futures,hashlib,json,pathlib,subprocess,urllib.request
from download import REPOS,ROOT,OUT

def fetch(item):
 key,repo=item
 meta=json.load(urllib.request.urlopen(f'https://huggingface.co/api/models/{repo}?blobs=true'))
 f=next(s for s in meta['siblings'] if s['rfilename'].lower().endswith('q8_0.gguf') and 'mmproj' not in s['rfilename'])
 url=f"https://huggingface.co/{repo}/resolve/{meta['sha']}/{f['rfilename']}";path=ROOT/'models'/(key+'-q8_0.gguf')
 if not path.exists():
  subprocess.run(['curl','-fL','--retry','3','-sS','-o',str(path)+'.part',url],check=True);pathlib.Path(str(path)+'.part').rename(path)
 sha=hashlib.file_digest(path.open('rb'),'sha256').hexdigest();assert sha==f['lfs']['sha256']
 r=dict(key=key+'-q8_0',repo=repo,revision=meta['sha'],url=url,sha256=sha,bytes=path.stat().st_size)
 (OUT/(key+'-q8-download.json')).write_text(json.dumps(r,indent=2));print(key,'verified',flush=True);return r
if __name__=='__main__':
 with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:records=list(pool.map(fetch,REPOS.items()))
 (OUT/'downloads-q8.json').write_text(json.dumps(records,indent=2)+'\n')
