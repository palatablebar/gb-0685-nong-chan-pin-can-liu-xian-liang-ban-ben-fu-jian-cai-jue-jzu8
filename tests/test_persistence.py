import base64,json,os,tempfile
import pytest
from app import store
from app.errors import DomainError
from app.workflow import load,run

@pytest.fixture
def db(tmp_path,monkeypatch):
    monkeypatch.setenv('DB_PATH',str(tmp_path/'p.db'))
    return tmp_path

def confirm(state):
    return store.review(state['run_id'],1,'通过')

def test_same_source_rerun_does_not_duplicate_projects(db):
    raw=load('basis.csv');a=run(raw,{})
    store.save(a['run_id'],a);b=run(raw,{})
    assert a['run_id']==b['run_id']
    saved=store.save(b['run_id'],b)
    assert len(saved['calculations'])==len(a['calculations']) and len(store.history())==1

def test_confirmed_conclusion_survives_rerun(db):
    raw=load('basis.csv');a=run(raw,{'ratio_csv':load('ratios.csv')})
    store.save(a['run_id'],a);confirm(a)
    rerun=run(raw,{'ratio_csv':load('ratios.csv')})
    saved=store.save(rerun['run_id'],rerun)
    assert rerun['run_id']==a['run_id']
    assert saved['decision']=='通过' and saved['review_version']==2
    assert [r['code'] for r in saved['calculations']]==[r['code'] for r in a['calculations']]

def test_supplement_creates_version_and_keeps_previous(db):
    raw=load('basis.csv');a=run(raw,{})
    store.save(a['run_id'],a)
    b=store.supplement(a['run_id'],[{'sample_id':'S001','basis':'全果','edible_ratio':'0.9'}])
    assert b['run_id']!=a['run_id'] and b['source_id']==a['source_id']
    assert len(b['calculations'])==2 and len(b['held'])==2
    assert store.previous(b['run_id'])['run_id']==a['run_id']
    assert store.history()[0]['id']==b['run_id']

def test_failed_supplement_leaves_batch_untouched(db):
    raw=load('basis.csv');a=run(raw,{})
    store.save(a['run_id'],a)
    with pytest.raises(DomainError) as e:store.supplement(a['run_id'],[{'sample_id':'S001','basis':'全果','edible_ratio':'1.5'}])
    assert e.value.code=='EVIDENCE'
    assert store.history()[0]['id']==a['run_id']
    assert len(store.get(a['run_id'])['calculations'])==1

def test_supplement_is_idempotent(db):
    raw=load('basis.csv');a=run(raw,{})
    store.save(a['run_id'],a)
    extra=[{'sample_id':'S003','basis':'全果','edible_ratio':'0.9'}]
    first=store.supplement(a['run_id'],extra);second=store.supplement(first['run_id'],extra)
    assert first['run_id']==second['run_id']
    assert len(second['calculations'])==len(first['calculations'])
    assert [h['id'] for h in store.history()].count(first['run_id'])==1

def test_restore_recovers_previous_version(db):
    raw=load('basis.csv');a=run(raw,{})
    store.save(a['run_id'],a)
    b=store.supplement(a['run_id'],[{'sample_id':'S001','basis':'全果','edible_ratio':'0.9'}])
    restored=store.restore(b['run_id'])
    assert restored['run_id']==a['run_id']
    assert restored['calculations']==store.get(a['run_id'])['calculations']
    assert restored['held']==store.get(a['run_id'])['held']
    assert store.history()[0]['id']==a['run_id']

def test_restore_without_previous_is_rejected(db):
    raw=load('basis.csv');a=run(raw,{})
    store.save(a['run_id'],a)
    with pytest.raises(DomainError) as e:store.restore(a['run_id'])
    assert e.value.code=='NO_PREVIOUS'

def test_confirmed_conclusions_cannot_change_or_drop(db):
    raw=load('basis.csv');a=run(raw,{'ratio_csv':load('ratios.csv')})
    store.save(a['run_id'],a);confirm(a)
    dropped=run(raw,{'ratio_csv':load('ratios_partial.csv')})
    with pytest.raises(DomainError) as e:store.save(dropped['run_id'],dropped)
    assert e.value.code=='CONFIRMED'
    assert store.history()[0]['id']==a['run_id']

def test_confirmed_projects_can_be_added_but_not_changed(db):
    raw=load('basis.csv');a=run(raw,{'ratio_csv':load('ratios_partial.csv')})
    store.save(a['run_id'],a);confirm(a)
    # 补齐缺失项目可以，已确认项目保持不变；新版本重新复核
    grown=store.supplement(a['run_id'],[{'sample_id':'S003','basis':'全果','edible_ratio':'0.9'}])
    assert len(grown['calculations'])==4 and grown['decision']=='待复核'
    old={r['id']:r['code'] for r in store.get(a['run_id'])['calculations']}
    assert {r['id']:r['code'] for r in grown['calculations']} == old | {'00003':'FAIL'}
    # 已确认项目的结论必须原样保留
    assert all(grown['calculations'][i]['code']==old[r['id']] for i,r in enumerate(grown['calculations']) if r['id'] in old)
    # 改变已确认项目结论需要先退回
    store.review(a['run_id'],2,'退回')
    changed=store.supplement(a['run_id'],[{'report_id':'00001','basis':'全果','edible_ratio':'0.95','version':'2'}])
    assert next(r for r in changed['calculations'] if r['id']=='00001')['code']=='PASS'

def test_archive_bundle_keeps_original_report(db):
    import csv,io
    from app.modules.m7_archive.export_out import bundle
    raw=load('basis.csv');a=run(raw,{'ratio_csv':load('ratios.csv')})
    store.save(a['run_id'],a)
    rows=list(csv.reader(io.StringIO(bundle(store.get(a['run_id'])).lstrip('﻿'))))
    kinds=[r[0] for r in rows]
    assert '原始报告' in kinds and '凭据原文' in kinds and '待补录' not in kinds
    report=json.loads(next(r[5] for r in rows if r[0]=='原始报告'))
    assert base64.b64decode(report['content_b64'])==raw
    held_bundle=bundle(run(load('basis.csv'),{}))
    assert '待补录' in held_bundle

def test_evidence_and_raw_source_are_archived(db):
    raw=load('basis.csv');a=run(raw,{'ratio_csv':load('ratios.csv')})
    store.save(a['run_id'],a)
    kept=store.get(a['run_id'])
    # 原始报告与比例凭据原文都可从归档中还原
    assert base64.b64decode(kept['source_b64'])==raw
    sources=kept['evidence']['sources']
    assert any(base64.b64decode(s['content_b64'])==load('ratios.csv') for s in sources)
    assert all(len(s['sha256'])==64 for s in sources)

def test_review_still_guards_stale_versions(db):
    raw=load('basis.csv');a=run(raw,{})
    store.save(a['run_id'],a)
    assert store.review(a['run_id'],1,'通过')['review_version']==2
    with pytest.raises(DomainError) as e:store.review(a['run_id'],1,'退回')
    assert e.value.status==409
