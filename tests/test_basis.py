import csv,io,os,tempfile
from decimal import Decimal
import pytest
from app.basis import (BASIS_EDIBLE,BASIS_WHOLE,BASIS_UNKNOWN,canonical_parameters,
    merge_evidence,normalize_basis,normalize_record,parse_ratio_csv,resolve_basis)
from app.errors import DomainError
from app.modules.m4_segments.rules import concentration
from app.modules.m5_calculate.rules import select_with_holds
from app.workflow import load,run

def test_basis_aliases_normalized():
    assert normalize_basis('全果基')==BASIS_WHOLE
    assert normalize_basis('可食部')==BASIS_EDIBLE
    assert normalize_basis('未注明')==BASIS_UNKNOWN
    assert normalize_basis('')==BASIS_UNKNOWN
    with pytest.raises(DomainError) as e:normalize_basis('整果')
    assert e.value.code=='BASIS'

def test_whole_fruit_is_converted_to_edible():
    whole=concentration('0.0045','mg/kg','0.001','0.005','0.9')
    assert Decimal(whole['lower'])==Decimal('0.005') and whole['code']=='PASS'
    # 若按批次统一口径（原值直接判）会被误判为 PASS，这里检查比例确实参与了换算
    assert concentration('0.0045','mg/kg','0.001','0.005')['upper']=='0.0045'

def test_edge_sample_flips_only_with_ratio_evidence():
    raw=load('basis.csv')
    without=run(raw,{})
    assert [r['id'] for r in without['calculations']]==['00002']
    assert {h['sample_id'] for h in without['held']}=={'S001','S003','S004'}
    partial=run(raw,{'ratio_csv':load('ratios_partial.csv')})
    # S001 全果 0.9：0.0045/0.9=0.005 恰等于限量，判 PASS；不能整批套用同一比例
    rows={r['id']:r for r in partial['calculations']}
    assert rows['00001']['code']=='PASS' and rows['00001']['concentration_basis']==BASIS_EDIBLE
    assert rows['00002']['edible_ratio']=='1'
    assert {h['sample_id'] for h in partial['held']}=={'S003'}
    full=run(raw,{'ratio_csv':load('ratios.csv')})
    assert full['reconcile']['sealable'] is True and not full['held']

def test_unknown_basis_waits_for_evidence_not_batch_default():
    raw=load('basis.csv')
    state=run(raw,{})
    held={h['sample_id']:h for h in state['held']}
    assert held['S003']['reason']=='BASIS_MISSING'
    assert held['S001']['reason']=='RATIO_MISSING'
    report=next(m for m in state['measurements'] if m['id']=='00001')
    assert report['basis_status']=='pending'
    # 全果报告缺比例凭据时不得给出判定
    chosen,bad,held_rows=select_with_holds(state['measurements'])
    assert all(r['id'] not in {'00001','00003','00004'} for r in chosen)
    assert len(held_rows)==3

def test_basis_conflict_is_quarantined():
    raw=load('basis.csv')
    text=raw.decode('utf-8-sig')
    state=run(load('basis.csv'),{'ratio_evidence':[{'sample_id':'S002','basis':'全果','edible_ratio':'0.8'}]})
    assert any(q['code']=='BASIS_CONFLICT' for q in state['quarantine'])
    assert all(r['id']!='00002' for r in state['calculations'])

def test_ratio_csv_parsing_and_archive_source():
    records,encoding=parse_ratio_csv(load('ratios.csv'))
    assert len(records)==3 and encoding=='utf-8-sig'
    params=canonical_parameters({'ratio_csv':load('ratios.csv')})
    assert len(params['ratios'])==3
    kinds={s['kind'] for s in params['supplement_sources']}
    assert {'ratio_csv','ratio_manifest'}<=kinds
    raw=params['supplement_sources'][0]['content_b64']
    assert raw

def test_duplicate_evidence_does_not_add_projects():
    once=canonical_parameters({'ratio_csv':load('ratios.csv')})
    twice=canonical_parameters({'ratios':once['ratios']+once['ratios']})
    assert len(once['ratios'])==len(twice['ratios'])==3
    first=run(load('basis.csv'),once);second=run(load('basis.csv'),twice)
    assert first['run_id']==second['run_id']
    assert len(first['calculations'])==4 and len(second['calculations'])==4

def test_conflicting_same_version_evidence_rejected():
    with pytest.raises(DomainError) as e:merge_evidence([], [
        {'report_id':'00001','basis':'全果','edible_ratio':'0.9','version':'1'},
        {'report_id':'00001','basis':'全果','edible_ratio':'0.8','version':'1'}])
    assert e.value.code=='EVIDENCE_CONFLICT'

def test_ratio_out_of_range_rejected():
    for ratio in ('0','1.2','-0.5'):
        with pytest.raises(DomainError) as e:normalize_record({'sample_id':'S1','basis':'全果','edible_ratio':ratio})
        assert e.value.code=='EVIDENCE'

def test_legacy_reports_keep_historical_default():
    state=run(load('clean.csv'),{})
    assert state['basis_summary']['legacy_default']==36
    assert state['reconcile']['sealable'] is True
    assert all(r['concentration_basis']==BASIS_EDIBLE for r in state['calculations'])

def test_inline_ratio_column_is_evidence_for_that_report_only():
    raw='''id,sample_id,batch,product,analyte,method,version,sampled,reported,result,unit,lod,relation,note,basis,edible_ratio
00001,S1,B1,苹果,P01,LC-MS/MS,1,2026-08-01,2026-08-02,0.0045,mg/kg,0.001,,,全果,0.9
00002,S2,B1,苹果,P01,LC-MS/MS,1,2026-08-01,2026-08-02,0.0045,mg/kg,0.001,,,全果,
'''.encode()
    state=run(raw,{})
    rows={r['id']:r for r in state['calculations']}
    assert rows['00001']['code']=='PASS' and rows['00001']['basis_evidence']=='REPORT-00001'
    # 同批另一份报告没有比例，不会被套用 S1 的 0.9
    assert [h['sample_id'] for h in state['held']]==['S2']

def test_batch_grade_is_provisional_when_any_project_held():
    state=run(load('basis.csv'),{})
    grade=state['reconcile']['grades'][0]
    assert grade['code'] is None and grade['published_code']=='PASS'
    assert grade['provisional'] is True and grade['sealable'] is False
    full=run(load('basis.csv'),{'ratio_csv':load('ratios.csv')})
    assert full['reconcile']['grades'][0]['code']=='FAIL' and full['reconcile']['sealable'] is True
