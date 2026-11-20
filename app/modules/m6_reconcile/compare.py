from collections import defaultdict
from app.basis import BASIS_EDIBLE

RANK={'PASS':0,'UNCERTAIN':1,'FAIL':2}


def balance(state):
    accepted=len(state['rows']);isolated=len(state['quarantine']);accounted=accepted+isolated+state['blank_count']+state['header_count']+state['duplicate_count']
    batches=defaultdict(list)
    for r in state['calculations']:batches[r['batch']].append(r)
    held=defaultdict(list)
    for h in state.get('held',[]):held[h['batch']].append(h)
    grades=[]
    for b,rows in sorted(batches.items()):
        published=max((r['code'] for r in rows),key=lambda c:RANK[c])
        held_rows=held.get(b,[])
        anchored=all(r.get('concentration_basis')==BASIS_EDIBLE for r in rows)
        complete=not held_rows and anchored
        grades.append({'batch':b,
            # 整批风险结论只在项目齐全时成立；有挂起项目时只给出已发布部分的风险。
            'code':published if complete else None,
            'published_code':published,
            'projects':len(rows),'samples':len({r['sample_id'] for r in rows}),
            'held':len(held_rows),
            # 批次判级、封存资格与项目结果锚定同一条可食部浓度依据。
            'basis':BASIS_EDIBLE if anchored else None,
            'complete':complete,'sealable':complete,'provisional':not complete})
    for b,held_rows in sorted(held.items()):
        if b in batches:continue
        grades.append({'batch':b,'code':None,'published_code':None,'projects':0,
            'samples':len({h['sample_id'] for h in held_rows}),
            'held':len(held_rows),'basis':None,'complete':False,'sealable':False,'provisional':True})
    grades.sort(key=lambda g:g['batch'])
    sealable=bool(grades) and all(g['sealable'] for g in grades) and all(r.get('concentration_basis')==BASIS_EDIBLE for r in state['calculations'])
    return {'input':state['input_count'],'accepted':accepted,'isolated':isolated,'blank':state['blank_count'],'headers':state['header_count'],'duplicates':state['duplicate_count'],'accounted':accounted,'balanced':accounted==state['input_count'],'business':state['business'],'grades':grades,
        'held':len(state.get('held',[])),'sealable':sealable,'basis':BASIS_EDIBLE}
