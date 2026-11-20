import time,importlib,json,base64
from pathlib import Path
from app.basis import canonical_parameters,evidence_state
from app.config import MODULES
from app.errors import DomainError
from app.manifest import identity,manifest,source_identity

def load(sample):
    if sample not in ('dirty.csv','clean.csv','legacy.csv','basis.csv','ratios.csv','ratios_partial.csv'):raise DomainError('SAMPLE','样例名称无效')
    return (Path(__file__).resolve().parents[1]/'sample_data'/sample).read_bytes()

def run(content,parameters=None,stage=7):
    if len(content)>2_000_000:raise DomainError('SIZE','文件超过2MB',413)
    # 参数（含晚到的比例凭据）先归一：同源重跑不会因列表顺序或重复凭据产生新项目。
    params=canonical_parameters(parameters or {})
    start=time.monotonic();state=None
    for n,entry in enumerate(MODULES,1):
        if n>stage:break
        fn=importlib.import_module('app.modules.'+entry[0]+'.service').execute
        state=fn(content,params) if n==1 else fn(state)
    # Decimal values are serialized as strings before any storage or response.
    state=json.loads(json.dumps(state,default=str,ensure_ascii=False))
    state['run_id']=identity(content,params);state['source_id']=source_identity(content)
    state['manifest']=manifest(content,params,time.monotonic()-start)
    state['source_b64']=base64.b64encode(content).decode('ascii')
    state['evidence']=evidence_state(params)
    if stage>=7:
        from app.modules.m7_archive.export_out import checksum
        state['archive']['sha256']=checksum(state)
    return state
