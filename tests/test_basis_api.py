import pytest

def run_basis(client,params=None):
    from app.workflow import load
    raw=load('basis.csv').decode('utf-8-sig')
    return client.post('/api/runs',json={'csv':raw,'parameters':params or {}})

def test_mixed_batch_publishes_only_resolved_projects(client):
    state=run_basis(client).get_json()
    assert [r['id'] for r in state['calculations']]==['00002']
    assert {h['sample_id'] for h in state['held']}=={'S001','S003','S004'}
    assert state['reconcile']['sealable'] is False

def test_export_blocked_until_evidence_complete(client):
    state=run_basis(client).get_json()
    blocked=client.get('/api/archive/'+state['run_id']+'/export.csv')
    assert blocked.status_code==422 and blocked.get_json()['code']=='NOT_SEALABLE'

def test_supplement_then_export_contains_basis_and_sources(client):
    from app.workflow import load
    state=run_basis(client).get_json()
    csv_text=load('ratios.csv').decode('utf-8-sig')
    sup=client.post('/api/runs/'+state['run_id']+'/evidence',json={'ratio_csv':csv_text})
    assert sup.status_code==200
    full=sup.get_json();assert full['reconcile']['sealable'] is True and not full['held']
    export=client.get('/api/archive/'+full['run_id']+'/export.csv')
    assert export.status_code==200
    body=export.data.decode('utf-8-sig')
    assert '凭据原文' in body and '可食部' in body

def test_bad_supplement_keeps_previous_batch(client):
    state=run_basis(client).get_json()
    bad=client.post('/api/runs/'+state['run_id']+'/evidence',
        json={'ratio_csv':'sample_id,basis,edible_ratio\nS001,全果,2\n'})
    assert bad.status_code==422 and bad.get_json()['code']=='EVIDENCE'
    current=client.get('/api/runs').get_json()[0]
    assert current['id']==state['run_id']
    assert len(client.get('/api/runs/'+state['run_id']).get_json()['calculations'])==1

def test_restore_endpoint(client):
    state=run_basis(client).get_json()
    sup=client.post('/api/runs/'+state['run_id']+'/evidence',
        json={'ratio_csv':'sample_id,basis,edible_ratio\nS001,全果,0.9\n'}).get_json()
    restored=client.post('/api/runs/'+sup['run_id']+'/restore')
    assert restored.status_code==200 and restored.get_json()['run_id']==state['run_id']

def test_evidence_endpoint_exposes_sources(client):
    from app.workflow import load
    state=run_basis(client,{'ratio_csv':load('ratios.csv').decode('utf-8-sig')}).get_json()
    evidence=client.get('/api/runs/'+state['run_id']+'/evidence').get_json()
    assert evidence['count']==3 and {s['kind'] for s in evidence['sources']}=={'ratio_csv','ratio_manifest'}
