import importlib,os
from flask import Flask,jsonify,request,redirect,Response
from app.config import MODULES
from app.errors import DomainError
from app.workflow import run,load
from app import store
from app.web import payload
from app.modules.m7_archive.export_out import bundle

def create_app():
    app=Flask(__name__);app.config['MAX_CONTENT_LENGTH']=2_000_000
    for entry in MODULES:app.register_blueprint(importlib.import_module('app.modules.'+entry[0]+'.handler').bp)
    @app.errorhandler(DomainError)
    def domain_error(error):return jsonify(code=error.code,message=error.message),error.status
    @app.errorhandler(404)
    def missing(error):return jsonify(code='NOT_FOUND',message='入口不存在'),404
    @app.get('/')
    def index():return redirect('/'+MODULES[0][0])
    @app.get('/healthz')
    def health():return jsonify(status='ok')
    @app.post('/api/runs')
    def submit():
        content,params=payload();state=run(content,params);return jsonify(store.save(state['run_id'],state))
    @app.get('/api/runs')
    def list_runs():return jsonify(store.history())
    @app.get('/api/runs/<run_id>')
    def get_run(run_id):return jsonify(store.get(run_id))
    @app.get('/api/runs/<run_id>/evidence')
    def get_evidence(run_id):
        state=store.get(run_id);return jsonify(state.get('evidence',{}))
    @app.post('/api/runs/<run_id>/restore')
    def restore_run(run_id):
        # 补录失败时恢复旧版批次结果，旧版项目与已确认结论原样保留。
        return jsonify(store.restore(run_id))
    @app.post('/api/runs/<run_id>/evidence')
    def supplement_run(run_id):
        # 晚到的比例凭据按批次补录；任何校验失败都不会改动当前批次结果。
        data=request.get_json(silent=True) or {}
        if not isinstance(data,dict):raise DomainError('PAYLOAD','请求必须是对象')
        raw=data.get('ratio_csv')
        if isinstance(raw,str):raw=raw.encode('utf-8')
        if not raw:raise DomainError('EVIDENCE','请提交比例凭据')
        from app.basis import decode_text,parse_ratio_csv
        text,encoding=decode_text(raw)
        records,_=parse_ratio_csv(text,encoding)
        return jsonify(store.supplement(run_id,records))
    @app.post('/api/runs/<run_id>/review')
    def review(run_id):
        data=request.get_json(silent=True) or {}
        if not isinstance(data.get('version'),int):raise DomainError('VERSION','请提交复核版本')
        return jsonify(store.review(run_id,data['version'],data.get('decision')))
    @app.get('/api/archive/<run_id>/export.csv')
    def download(run_id):
        state=store.get(run_id)
        if not state.get('reconcile',{}).get('balanced'):raise DomainError('UNBALANCED','对账未平')
        if not state.get('reconcile',{}).get('sealable'):raise DomainError('NOT_SEALABLE','仍有待补录项目，暂不可封存')
        return Response(bundle(state),mimetype='text/csv; charset=utf-8',headers={'Content-Disposition':'attachment; filename=evidence.csv'})
    with app.app_context():
        if not store.history():
            state=run(load('dirty.csv'),{});store.save(state['run_id'],state)
    return app

app=create_app()
if __name__=='__main__':app.run(host='0.0.0.0',port=8000)
