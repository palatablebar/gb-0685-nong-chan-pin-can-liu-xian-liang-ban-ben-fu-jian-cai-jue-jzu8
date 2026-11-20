from decimal import Decimal, InvalidOperation
import unicodedata
from app.basis import BASIS_EDIBLE, BASIS_WHOLE, resolve_basis
from app.errors import DomainError


def concentration(result, unit, lod, limit, basis_ratio=Decimal(1)):
    factors={'mg/kg':Decimal(1),'ppm':Decimal(1),'ug/kg':Decimal('.001'),'ppb':Decimal('.001')}
    if unit not in factors:raise DomainError('UNIT','检出量单位无效')
    ratio=Decimal(str(basis_ratio))
    if ratio<=0:raise DomainError('RANGE','可食部比例无效')
    # 全果基浓度除以可食部比例，得到可食部基浓度；避免先取倒数再相乘引入误差。
    factor=factors[unit]/ratio;detection=Decimal(str(lod))*factors[unit]/ratio;threshold=Decimal(str(limit))
    text=unicodedata.normalize('NFKC',str(result)).strip().upper()
    try:
        if text=='ND':lower,upper,exclusive=Decimal(0),detection,True
        elif text.startswith('<'):
            value=Decimal(text[1:])*factor
            if not value.is_finite() or value<=0 or value!=detection:raise DomainError('RANGE','低于检出限报告与LOD不一致')
            lower,upper,exclusive=Decimal(0),value,True
        else:
            value=Decimal(text.replace(',',''))*factor
            if not value.is_finite() or value<0 or value<detection:raise DomainError('RANGE','定量值低于检出限')
            lower,upper,exclusive=value,value,False
    except InvalidOperation:raise DomainError('NUMBER','检出量无法解析')
    code='PASS' if upper<=threshold else 'FAIL' if lower>threshold else 'UNCERTAIN'
    return {'lower':str(lower),'upper':str(upper),'upper_exclusive':exclusive,'limit':str(threshold),'code':code,'unit':'mg/kg'}


def apply(state):
    params=state.get('parameters') or {}
    records=list(params.get('ratios') or [])
    # 逐份判断：报告根本没有基准列时，无凭据则延续历史默认可食部基；报告带
    # 基准列但未注明时，必须等待凭据。绝不用整批共用一条比例。
    legacy_default=params.get('legacy_basis_default',True) is not False
    measurements=[];bad=[];pending=[]
    for r in state['reports']:
        try:
            basis=resolve_basis(r,records,legacy_default=legacy_default)
            m=dict(r)
            m.update({'reported_basis':basis.get('source_basis',''),
                      'basis_evidence':basis.get('evidence_id'),
                      'basis_status':basis['status']})
            if basis['status']=='pending':
                m.update({'basis':basis['basis'],'basis_reason':basis['reason'],
                          'concentration_basis':None,'edible_ratio':None})
                pending.append(m)
            else:
                ratio=Decimal(basis['edible_ratio'])
                m.update(concentration(r['result'],r['unit'],r['lod'],r['limit'],ratio))
                m.update({'basis':basis['basis'],'concentration_basis':BASIS_EDIBLE,
                          'edible_ratio':str(ratio),'basis_converted':basis['converted'],
                          'basis_legacy_default':basis.get('legacy_default',False)})
            measurements.append(m)
        except DomainError as e:bad.append((r,e.code))
    lines={r['_line'] for r,_ in bad};state['rows']=[r for r in state['rows'] if r['_line'] not in lines]
    for r,c in bad:state['quarantine'].append({'line':r['_line'],'id':r['id'],'code':c,'raw':r['_raw']})
    state['measurements']=measurements
    # 单份报告级待补录：报告自身缺基准或全果基缺比例凭据。复检裁决会据此
    # 按样本/项目整体挂起，但仍能先发布其他有依据的项目。
    state['pending']=[{'line':m['_line'],'report_id':m['id'],'sample_id':m['sample_id'],
        'batch':m['batch'],'product':m['product'],'analyte':m['analyte'],
        'reason':m.get('basis_reason'),'reported_basis':m.get('reported_basis',''),
        'evidence_id':m.get('basis_evidence')} for m in pending]
    state['basis_summary']={'target_basis':BASIS_EDIBLE,'evidence_count':len(records),
        'resolved':len(measurements)-len(pending),'pending':len(pending),
        'whole_reported':sum(1 for m in measurements if m.get('basis')==BASIS_WHOLE),
        'converted':sum(1 for m in measurements if m.get('basis_converted')),
        'legacy_default':sum(1 for m in measurements if m.get('basis_legacy_default'))}
    return state
