from collections import defaultdict
from decimal import Decimal
from app.basis import BASIS_EDIBLE
from app.errors import DomainError


def _newest(rows):
    rank=max((r['reported'],Decimal(str(r['version']))) for r in rows)
    return [r for r in rows if (r['reported'],Decimal(str(r['version'])))==rank]


def select_reports(reports):
    """选取每样本/项目唯一可发布的原始或复检报告。

    返回 (chosen, bad)。无法确定可食部浓度依据的报告不进入 chosen，
    由 :func:`select_with_holds` 以 held 形式返回，绝不按批次默认比例放行。
    """
    chosen,bad,_=select_with_holds(reports);return chosen,bad


def select_with_holds(reports):
    versions={}
    for r in reports:
        if r['id'] not in versions or Decimal(str(r['version']))>Decimal(str(versions[r['id']]['version'])):versions[r['id']]=r
    reports=list(versions.values());lookup={r['id']:r for r in reports};bad={};valid=[];held=[]
    # A relation is a replacement chain; detect cycles before following any edge.
    for r in reports:
        chain=[];current=r
        while current.get('relation'):
            if current['id'] in chain:
                for rid in chain:bad[rid]='REFERENCE'
                break
            chain.append(current['id']);parent=lookup.get(current['relation'])
            if parent is None:bad[r['id']]='REFERENCE';break
            shared=('sample_id','batch','product','analyte','method','sampled')
            if any(current[k]!=parent[k] for k in shared) or current['reported']<=parent['reported']:
                bad[r['id']]='REFERENCE';break
            current=parent
        if current['id'] in bad:bad[r['id']]='REFERENCE'
    grouped=defaultdict(list)
    for r in reports:
        if r['id'] not in bad:grouped[(r['sample_id'],r['batch'],r['product'],r['analyte'])].append(r)
    for key,rows in grouped.items():
        roots=[r for r in rows if not r['relation']]
        if len(roots)!=1:
            for r in rows:bad[r['id']]='CONFLICT'
            continue
        # Only reports connected to the single primary sample result may supersede it.
        candidates=[r for r in rows if r['id'] not in bad]
        newest=_newest(candidates)
        pending=[r for r in newest if r.get('basis_status')=='pending']
        resolved=[r for r in newest if r.get('basis_status')!='pending']
        if pending:
            # 最新报告缺少可食部依据：该项目整体待补录，旧报告不能抢先发布。
            held.append({'sample_id':key[0],'batch':key[1],'product':key[2],'analyte':key[3],
                'reason':pending[0].get('basis_reason','BASIS_MISSING'),
                'root_id':roots[0]['id'],'pending_ids':sorted(r['id'] for r in pending),
                'reported_basis':pending[0].get('reported_basis',''),
                'evidence_id':next((r.get('basis_evidence') for r in pending if r.get('basis_evidence')),None)})
            continue
        if len({(r['lower'],r['upper']) for r in resolved})>1:
            for r in rows:bad[r['id']]='CONFLICT'
            continue
        chosen=sorted(resolved,key=lambda x:x['id'])[0];valid.append(chosen)
    return valid,bad,held


def apply(state):
    chosen,bad,held=select_with_holds(state['measurements']);lines=set()
    for r in state['measurements']:
        if r['id'] in bad:
            lines.add(r['_line']);state['quarantine'].append({'line':r['_line'],'id':r['id'],'code':bad[r['id']],'raw':r['_raw']})
    state['rows']=[r for r in state['rows'] if r['_line'] not in lines]
    calculations=[{'id':r['id'],'line':r['_line'],'sample_id':r['sample_id'],'batch':r['batch'],'analyte':r['analyte'],'method':r['method'],'standard':r['standard_id'],'lower':r['lower'],'upper':r['upper'],'upper_exclusive':r['upper_exclusive'],'limit':r['limit'],'code':r['code'],'replaces':r['relation'],
        # 判定与封存都锚定到同一条可食部浓度依据。
        'concentration_basis':r.get('concentration_basis'),'edible_ratio':r.get('edible_ratio'),
        'reported_basis':r.get('reported_basis'),'basis_evidence':r.get('basis_evidence'),
        'basis_converted':r.get('basis_converted',False),'basis_legacy_default':r.get('basis_legacy_default',False)} for r in chosen]
    state['calculations']=calculations;state['held']=held
    state['basis_summary'].update({'published':len(calculations),'held':len(held),
        'anchor':BASIS_EDIBLE if all(r.get('concentration_basis')==BASIS_EDIBLE for r in calculations) else None})
    state['business']={'total':len(calculations),'pass':sum(r['code']=='PASS' for r in calculations),'fail':sum(r['code']=='FAIL' for r in calculations),'uncertain':sum(r['code']=='UNCERTAIN' for r in calculations),'unit':'检测项目'};return state
