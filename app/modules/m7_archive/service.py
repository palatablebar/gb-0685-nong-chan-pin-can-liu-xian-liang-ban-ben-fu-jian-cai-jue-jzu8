from app.modules.m7_archive.export_out import checksum

def execute(state):
    reconcile=state.get('reconcile',{})
    # 封存资格同时要求：对账平、整批风险结论完整、全部项目锚定可食部浓度依据。
    ready=bool(reconcile.get('balanced')) and bool(reconcile.get('sealable'))
    state['archive']={'sha256':checksum(state),'evidence_count':len(state.get('calculations',[]))+len(state.get('held',[]))+len(state['quarantine']),
        'ready':ready,'basis':reconcile.get('basis'),'held':len(state.get('held',[])),
        'sealable':bool(reconcile.get('sealable'))}
    return state
