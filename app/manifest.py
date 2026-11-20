import hashlib,json,time
from datetime import datetime,timezone
from app.config import RULE_VERSION

def identity(content, parameters):
    # 身份只覆盖会影响结论的参数；凭据原文留档但不改变同一裁决的身份，
    # 因此重复补录同一凭据不会形成新的批次版本。
    stable={key:value for key,value in (parameters or {}).items() if key!='supplement_sources'}
    raw = content + json.dumps(stable,sort_keys=True,ensure_ascii=False).encode() + RULE_VERSION.encode()
    return hashlib.sha256(raw).hexdigest()[:20]

def source_identity(content):
    """批次来源身份：只由原始报告字节与规则版本决定。

    比例凭据晚到时来源身份不变，补录只会形成新的裁决版本，不会另起一批。
    """
    raw = content + RULE_VERSION.encode()
    return hashlib.sha256(raw).hexdigest()[:20]

def manifest(content, parameters, elapsed):
    return {'sha256':hashlib.sha256(content).hexdigest(),'rule_version':RULE_VERSION,
        'parameters':parameters,'elapsed_ms':round(elapsed*1000),
        'generated_at':datetime.now(timezone.utc).isoformat(),'timezone':'Asia/Shanghai'}
