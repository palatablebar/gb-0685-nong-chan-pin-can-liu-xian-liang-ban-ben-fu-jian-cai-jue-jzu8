import csv,io
from app.errors import DomainError
from app.config import REQUIRED
from app.modules.m1_ingest.decode import decode

HEADER_ALIASES={'prep_basis':'basis','制备基准':'basis','可食部基准':'basis','浓度基准':'basis','检测基准':'basis','可食部比例':'edible_ratio'}

def read(raw):
    text,encoding=decode(raw)
    if not text:raise DomainError('EMPTY_FILE','文件为空')
    if not text.strip():raise DomainError('BLANK_FILE','文件仅含空行')
    reader=csv.reader(io.StringIO(text,newline=''))
    try:raw_header=[s.strip().lstrip('\ufeff') for s in next(reader)]
    except (StopIteration,csv.Error):raise DomainError('HEADER','无法读取表头')
    # 制备基准列允许使用业务表头别名，统一归一为 basis。
    header=[HEADER_ALIASES.get(name,name) for name in raw_header]
    if len(set(header))!=len(header) or not set(REQUIRED)<=set(header):raise DomainError('HEADER','表头缺失或重复')
    out={'rows':[],'quarantine':[],'input_count':0,'blank_count':0,'header_count':0,'duplicate_count':0,'encoding':encoding,'header':header}
    try:
        for row in reader:
            out['input_count']+=1;line=reader.line_num
            if not row or not any(s.strip() for s in row):out['blank_count']+=1;continue
            if row==raw_header:out['header_count']+=1;continue
            if len(row)!=len(header):out['quarantine'].append({'line':line,'id':'','code':'WIDTH','raw':row});continue
            item=dict(zip(header,row));item['_line']=line;item['_raw']=dict(item);out['rows'].append(item)
    except csv.Error as exc:raise DomainError('CSV',str(exc))
    if out['input_count']==0:raise DomainError('HEADER_ONLY','文件仅含表头')
    return out
