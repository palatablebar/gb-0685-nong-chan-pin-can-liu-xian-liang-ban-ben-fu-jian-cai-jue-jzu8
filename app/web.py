import base64
from flask import request,render_template,jsonify
from app.basis import canonical_parameters,decode_text,parse_ratio_csv
from app.errors import DomainError
from app.config import TITLE,MODULES,SHORT
from app.workflow import run,load
from app import store

def _parameters(data):
    params=dict(data.get('parameters') or {})
    if params and not isinstance(params,dict):raise DomainError('PARAMETERS','参数必须是对象')
    # 比例凭据可以随参数、顶层字段或独立 CSV 补录；原文一律留档。
    if 'ratios' in data:params['ratios']=data['ratios']
    if 'ratio_evidence' in data:params['ratio_evidence']=data['ratio_evidence']
    if 'ratio_csv' in data:params['ratio_csv']=data['ratio_csv']
    return canonical_parameters(params)

def payload():
    data=request.get_json(silent=True) or {}
    if not isinstance(data,dict):raise DomainError('PAYLOAD','请求必须是对象')
    if 'csv' in data and not isinstance(data['csv'],str):raise DomainError('CSV','CSV内容必须是文本')
    sample=data.get('sample',request.args.get('sample','dirty.csv'))
    content=data.get('csv')
    return (content.encode() if isinstance(content,str) else load(sample)),_parameters(data)

def api(stage):
    content,parameters=payload();return jsonify(run(content,parameters,stage))

def page(stage,template):
    latest=store.history();state=store.get(latest[0]['id']) if latest else run(load('dirty.csv'),{})
    source=load(request.args['sample']) if 'sample' in request.args else base64.b64decode(state['source_b64']) if state.get('source_b64') else load('dirty.csv')
    data=run(source,state.get('manifest',{}).get('parameters',{}),stage)
    record=store.batch(state['run_id']) or {}
    return render_template(template,title=TITLE,short=SHORT,modules=MODULES,current=stage,data=data,run=state,history=latest,
        previous_run_id=record.get('previous_run_id'))
