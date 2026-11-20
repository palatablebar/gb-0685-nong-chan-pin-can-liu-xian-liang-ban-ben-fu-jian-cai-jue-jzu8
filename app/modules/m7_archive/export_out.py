import io,csv,json,hashlib

BASIS_LABEL='可食部'


def _record(writer,kind,batch,line,business_id,code,payload):
    writer.writerow([kind,batch,line,business_id,code,json.dumps(payload,ensure_ascii=False,sort_keys=True)])


def bundle(state):
    out=io.StringIO(newline='');writer=csv.writer(out)
    writer.writerow(['记录类型','批次','来源行','业务ID','原因码','内容'])
    rid=state.get('run_id','preview')
    _record(writer,'运行清单',rid,'','','',state.get('manifest',{}))
    if state.get('source_b64'):
        # 原始报告随证据一起留档，避免只存解析结果导致原文丢失。
        _record(writer,'原始报告',rid,'','','',{'report_sha256':state.get('manifest',{}).get('sha256'),
            'encoding':state.get('encoding',''),'content_b64':state.get('source_b64')})
    _record(writer,'裁决版本',rid,'','','',{'source_id':state.get('source_id'),'basis':BASIS_LABEL,
        'basis_summary':state.get('basis_summary',{})})
    _record(writer,'对账',rid,'','','',state.get('reconcile',{}))
    for row in state.get('calculations',[]):
        # 项目结果锚定到可食部浓度依据。
        payload=dict(row,basis=BASIS_LABEL)
        _record(writer,'计算',rid,row.get('line',''),row.get('id',''),row.get('code',''),payload)
    for row in state.get('held',[]):
        # 待补录的项目同样留档，说明为什么没有发布。
        _record(writer,'待补录',rid,row.get('root_id',''),row.get('sample_id',''),
            row.get('reason',''),dict(row,basis=BASIS_LABEL))
    for row in state.get('quarantine',[]):
        raw=row.get('raw',{})
        payload=dict(raw,code=row['code']) if isinstance(raw,dict) else {'raw':raw,'code':row['code']}
        _record(writer,'隔离',rid,row['line'],row.get('id',''),row['code'],payload)
    for source in state.get('evidence',{}).get('sources',[]):
        _record(writer,'凭据原文',rid,'',source.get('kind',''),'',source)
    return '﻿'+out.getvalue()

def checksum(state):
    return hashlib.sha256(bundle(state).encode('utf-8')).hexdigest()
