import sqlite3,json,os
from datetime import datetime,timezone
from pathlib import Path
from app.errors import DomainError

_SCHEMA=(
    'CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, body TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1, decision TEXT NOT NULL DEFAULT "待复核")',
    'CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY,value TEXT NOT NULL)',
    'CREATE TABLE IF NOT EXISTS batches (source_id TEXT PRIMARY KEY, current_run_id TEXT NOT NULL, previous_run_id TEXT, updated_at TEXT NOT NULL)',
)
_RUN_COLUMNS={'source_id':"TEXT NOT NULL DEFAULT ''",'created_at':'TEXT','superseded_by':'TEXT'}


def connection():
    path=Path(os.environ.get('DB_PATH','data/records.db'));path.parent.mkdir(parents=True,exist_ok=True)
    db=sqlite3.connect(path,timeout=10);db.row_factory=sqlite3.Row
    db.execute('PRAGMA journal_mode=WAL')
    for statement in _SCHEMA:db.execute(statement)
    columns={row[1] for row in db.execute('PRAGMA table_info(runs)')}
    for name,declaration in _RUN_COLUMNS.items():
        if name not in columns:db.execute(f'ALTER TABLE runs ADD COLUMN {name} {declaration}')
    return db


def _now():return datetime.now(timezone.utc).isoformat()


def _project_key(row):
    return (row.get('sample_id'),row.get('batch'),row.get('product'),row.get('analyte'))


def _conclusion(row):
    return (row.get('id'),row.get('lower'),row.get('upper'),row.get('limit'),row.get('code'),
            row.get('concentration_basis'),row.get('edible_ratio'),row.get('basis_evidence'))


def _guard_confirmed(previous, body):
    """已确认结论不得被重新运行覆盖。"""
    if not previous or previous.get('decision')!='通过':return
    old={_project_key(r):_conclusion(r) for r in previous.get('calculations',[])}
    new={_project_key(r):_conclusion(r) for r in body.get('calculations',[])}
    for key,value in old.items():
        if key in new and new[key]!=value:
            raise DomainError('CONFIRMED','已确认结论不得被覆盖，请先退回后再补录')
    if not set(old)<=set(new):
        raise DomainError('CONFIRMED','已确认结论不得减少，请先退回后再补录')


def _same_conclusions(previous, body):
    if not previous:return False
    old={_project_key(r):_conclusion(r) for r in previous.get('calculations',[])}
    new={_project_key(r):_conclusion(r) for r in body.get('calculations',[])}
    if old!=new:return False
    old_held=sorted((h.get('sample_id'),h.get('analyte'),h.get('reason')) for h in previous.get('held',[]))
    new_held=sorted((h.get('sample_id'),h.get('analyte'),h.get('reason')) for h in body.get('held',[]))
    return old_held==new_held


def save(run_id, body):
    """幂等保存一个裁决版本。

    同一 run_id 重复保存不会新增记录，也不会重置复核结论；同一来源的
    新版本会把旧版本记入 previous_run_id，便于补录失败或人工退回时恢复。
    如果新版本只是同源重跑、结论完全一致，则沿用已确认的复核状态。
    """
    source_id=body.get('source_id') or run_id
    with connection() as db:
        batch=db.execute('SELECT * FROM batches WHERE source_id=?',(source_id,)).fetchone()
        current=db.execute('SELECT * FROM runs WHERE id=?',(batch['current_run_id'],)).fetchone() if batch else None
        previous_body=json.loads(current['body']) if current else None
        if previous_body:previous_body.update(decision=current['decision'],review_version=current['version'])
        _guard_confirmed(previous_body,body)
        existing=db.execute('SELECT * FROM runs WHERE id=?',(run_id,)).fetchone()
        if existing is None:
            version=(current['version']+1) if current else 1
            carried=bool(current) and current['decision']=='通过' and _same_conclusions(previous_body,body)
            # 新的裁决版本原则上重新复核；同源重跑且结论一致时不丢已确认结论。
            decision='通过' if carried else '待复核'
            db.execute('INSERT INTO runs(id,source_id,body,version,decision,created_at) VALUES (?,?,?,?,?,?)',
                (run_id,source_id,json.dumps(body,ensure_ascii=False),version,decision,_now()))
        else:
            # 同一 run_id 即同一来源与参数：保留首次落库的正文与复核状态，
            # 重跑不会重复增加项目，也不会改动已确认结论。
            db.execute('UPDATE runs SET source_id=? WHERE id=?',(source_id,run_id))
        if batch is None:
            db.execute('INSERT INTO batches(source_id,current_run_id,previous_run_id,updated_at) VALUES (?,?,?,?)',
                (source_id,run_id,None,_now()))
        elif batch['current_run_id']!=run_id:
            db.execute('UPDATE batches SET previous_run_id=?,current_run_id=?,updated_at=? WHERE source_id=?',
                (batch['current_run_id'],run_id,_now(),source_id))
            db.execute('UPDATE runs SET superseded_by=? WHERE id=?',(run_id,batch['current_run_id']))
        else:
            db.execute('UPDATE batches SET updated_at=? WHERE source_id=?',(_now(),source_id))
        db.execute("INSERT INTO meta(key,value) VALUES ('latest',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(run_id,))
    return get(run_id)


def get(run_id):
    with connection() as db:r=db.execute('SELECT * FROM runs WHERE id=?',(run_id,)).fetchone()
    if not r:raise DomainError('NOT_FOUND','批次不存在',404)
    body=json.loads(r['body']);body.update(run_id=r['id'],review_version=r['version'],decision=r['decision'],
        source_id=r['source_id'],superseded_by=r['superseded_by'])
    return body


def batch(run_id):
    with connection() as db:
        row=db.execute('SELECT * FROM batches WHERE current_run_id=? OR previous_run_id=? OR source_id=?',(run_id,run_id,run_id)).fetchone()
    return dict(row) if row else None


def previous(run_id):
    record=batch(run_id)
    if not record or not record.get('previous_run_id'):return None
    return get(record['previous_run_id'])


def restore(run_id):
    """把批次结果恢复为上一裁决版本。"""
    record=batch(run_id)
    if not record or not record.get('previous_run_id'):raise DomainError('NO_PREVIOUS','没有可恢复的旧版批次结果')
    if record['current_run_id']!=run_id:raise DomainError('STATE','只能恢复当前批次结果')
    old=record['previous_run_id'];current=record['current_run_id']
    with connection() as db:
        db.execute('UPDATE batches SET current_run_id=?,previous_run_id=NULL,updated_at=? WHERE source_id=?',(old,_now(),record['source_id']))
        db.execute('UPDATE runs SET superseded_by=NULL WHERE id=?',(old,))
        db.execute('UPDATE runs SET superseded_by=? WHERE id=?',(old,current))
        db.execute("INSERT INTO meta(key,value) VALUES ('latest',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(old,))
    return get(old)


def supplement(run_id, extra):
    """向已有批次补录比例凭据；失败时原批次结果保持不变。

    补录沿用同一份原始报告字节，只叠加凭据，形成新的裁决版本。
    同一来源重跑不会重复增加项目。
    """
    import base64
    from app.workflow import run
    from app.basis import merge_evidence
    state=get(run_id)
    params=dict(state.get('manifest',{}).get('parameters') or {})
    previous_sources=list(params.get('supplement_sources') or [])
    merged=merge_evidence(params.get('ratios') or [], extra)
    params['ratios']=merged
    try:
        content=base64.b64decode(state['source_b64'])
        fresh=run(content,{'ratios':merged})
    except DomainError:
        # 补录失败：不落库，当前批次与原结论保持原样。
        raise
    # 凭据原文按内容哈希累积，旧批次已留档的凭据不会因新一轮补录而丢失。
    fresh['evidence']['sources']=sorted(
        {s['sha256']:s for s in previous_sources+fresh['evidence'].get('sources',[])}.values(),
        key=lambda item:item['sha256'])
    from app.modules.m7_archive.export_out import checksum
    fresh['archive']['sha256']=checksum(fresh)
    fresh['manifest']['parameters']=dict(fresh['manifest'].get('parameters',{}),supplement_sources=fresh['evidence']['sources'])
    if fresh['run_id']==run_id:
        return save(run_id,fresh)
    return save(fresh['run_id'],fresh)


def history():
    with connection() as db:rows=db.execute("SELECT id,version,decision,source_id,superseded_by FROM runs ORDER BY CASE WHEN id=(SELECT value FROM meta WHERE key='latest') THEN 0 ELSE 1 END,rowid DESC LIMIT 100").fetchall()
    return [dict(r) for r in rows]


def review(run_id, version, decision):
    if decision not in ('通过','退回'):raise DomainError('DECISION','复核状态无效')
    with connection() as db:
        cur=db.execute('UPDATE runs SET version=version+1,decision=? WHERE id=? AND version=?',(decision,run_id,version))
        if cur.rowcount!=1:
            if not db.execute('SELECT 1 FROM runs WHERE id=?',(run_id,)).fetchone():raise DomainError('NOT_FOUND','批次不存在',404)
            raise DomainError('STALE','复核版本已更新，请刷新',409)
    return get(run_id)
