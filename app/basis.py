"""制备基准与可食部比例凭据。

外检报告可能按全果基出具，也可能未注明制备基准；内控限量统一按可食部基。
本模块只做两件事：

1. 把报告基准与晚到的比例凭据解析成可复核的结构；
2. 逐份报告推导"可食部浓度依据"，绝不按批次统一套用比例。

未取得比例凭据的报告不会被猜测成可食部基，而是进入待补录状态；已经能
确定基准的报告仍可单独发布，避免整批被一起压住。
"""

import base64
import csv
import hashlib
import io
import json
import unicodedata
from decimal import Decimal, InvalidOperation

from app.errors import DomainError

BASIS_EDIBLE = '可食部'
BASIS_WHOLE = '全果'
BASIS_UNKNOWN = '未注明'
TARGET_BASIS = BASIS_EDIBLE

_SENTINELS = {'', '-', 'N/A', 'NULL', '未知', '未注明', '—', '--'}
_BASIS_ALIASES = {
    '可食部': BASIS_EDIBLE,
    '可食部基': BASIS_EDIBLE,
    '可食部分': BASIS_EDIBLE,
    'EDIBLE': BASIS_EDIBLE,
    'EDIBLEPORTION': BASIS_EDIBLE,
    '全果': BASIS_WHOLE,
    '全果基': BASIS_WHOLE,
    'WHOLE': BASIS_WHOLE,
    'WHOLEFRUIT': BASIS_WHOLE,
}
_FIELDS = ('evidence_id', 'report_id', 'sample_id', 'batch', 'product', 'basis',
           'declared_basis', 'edible_ratio', 'version', 'source', 'note')


def _text(value):
    return unicodedata.normalize('NFKC', '' if value is None else str(value)).strip()


def normalize_basis(value):
    """把报告中的人类可读基准归一为三种内部值。"""
    text = _text(value)
    if text.upper() in _SENTINELS:
        return BASIS_UNKNOWN
    key = text.upper().replace(' ', '')
    if key in _BASIS_ALIASES:
        return _BASIS_ALIASES[key]
    if key.endswith('基') and key[:-1] in _BASIS_ALIASES:
        return _BASIS_ALIASES[key[:-1]]
    raise DomainError('BASIS', f'制备基准无法识别：{text}')


def _decimal(value, code, message):
    text = _text(value)
    if text.upper() in _SENTINELS:
        return None
    try:
        number = Decimal(text)
        if not number.is_finite():
            raise InvalidOperation
        return number
    except InvalidOperation:
        raise DomainError(code, message)


def _stable_id(record):
    raw = '|'.join([record.get('report_id', ''), record.get('sample_id', ''),
                    record.get('product', ''), record.get('version', '1')])
    return 'EV-' + hashlib.sha256(raw.encode('utf-8')).hexdigest()[:10]


def normalize_record(raw, line=None):
    """校验并归一一条比例凭据。"""
    if not isinstance(raw, dict):
        raise DomainError('EVIDENCE', '比例凭据必须是对象')
    item = {key: _text(raw.get(key)) for key in _FIELDS if key in raw}
    sample_id = item.get('sample_id', '')
    report_id = item.get('report_id', '')
    if not sample_id and not report_id:
        raise DomainError('EVIDENCE', '比例凭据缺少样本或报告标识')

    declared = normalize_basis(item.get('basis', ''))
    ratio = _decimal(item.get('edible_ratio'), 'EVIDENCE', '可食部比例无效')
    if ratio is not None and not (Decimal(0) < ratio <= Decimal(1)):
        raise DomainError('EVIDENCE', '可食部比例必须大于0且不超过1')

    version = _decimal(item.get('version') or '1', 'EVIDENCE', '凭据版本无效')
    if version is None or version <= 0:
        raise DomainError('EVIDENCE', '凭据版本必须为正数')

    if declared == BASIS_UNKNOWN:
        if ratio is None:
            raise DomainError('EVIDENCE', '未声明基准的比例凭据必须给出可食部比例')
        declared = BASIS_WHOLE if ratio < 1 else BASIS_EDIBLE
    if declared == BASIS_WHOLE and ratio is None:
        raise DomainError('EVIDENCE', '全果基凭据必须给出可食部比例')
    if declared == BASIS_EDIBLE:
        if ratio is not None and ratio != 1:
            raise DomainError('EVIDENCE', '可食部基凭据的比例必须为1')
        ratio = Decimal(1)

    record = {
        'sample_id': sample_id,
        'report_id': report_id,
        'batch': item.get('batch', ''),
        'product': item.get('product', ''),
        'basis': declared,
        'declared_basis': normalize_basis(item.get('basis', '')) if item.get('basis', '') else BASIS_UNKNOWN,
        'edible_ratio': str(ratio),
        'version': str(version),
        'source': item.get('source', ''),
        'note': item.get('note', ''),
    }
    if line is not None:
        record['line'] = line
    record['evidence_id'] = item.get('evidence_id') or _stable_id(record)
    return record


def parse_ratio_csv(text, encoding='utf-8-sig'):
    """解析补录的比例凭据 CSV。"""
    if isinstance(text, bytes):
        text, encoding = decode_text(text)
    text = text.lstrip('﻿')
    if not text.strip():
        raise DomainError('EVIDENCE', '比例凭据为空')
    try:
        reader = csv.DictReader(io.StringIO(text, newline=''))
        header = [str(name).strip().lstrip('﻿') for name in (reader.fieldnames or [])]
        if 'sample_id' not in header and 'report_id' not in header:
            raise DomainError('EVIDENCE', '比例凭据缺少样本或报告标识列')
        records = []
        for line, row in enumerate(reader, 2):
            if not row or not any(_text(value) for value in row.values()):
                continue
            records.append(normalize_record(row, line=line))
    except csv.Error as error:
        raise DomainError('EVIDENCE', f'比例凭据无法解析：{error}')
    if not records:
        raise DomainError('EVIDENCE', '比例凭据没有有效记录')
    return records, encoding


def decode_text(raw):
    for encoding in ('utf-8-sig', 'gb18030'):
        try:
            return raw.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    raise DomainError('ENCODING', '比例凭据编码无法识别')


def _key(record):
    return (record.get('report_id', ''), record.get('sample_id', ''),
            record.get('product', ''), record.get('batch', ''),
            record.get('version', '1'))


def _canonical(record):
    return json.dumps({key: record.get(key, '') for key in _FIELDS},
                      ensure_ascii=False, sort_keys=True)


def merge_evidence(existing, incoming):
    """按（报告/样本, 项目, 批次, 版本）合并凭据；同版冲突直接失败。"""
    merged = {}
    for record in list(existing or []) + list(incoming or []):
        if not isinstance(record, dict):
            raise DomainError('EVIDENCE', '比例凭据必须是对象')
        item = normalize_record(record)
        key = _key(item)
        if key in merged and _canonical(merged[key]) != _canonical(item):
            raise DomainError('EVIDENCE_CONFLICT', '同一比例凭据版本内容冲突')
        merged[key] = item
    return sorted(merged.values(), key=lambda row: (_key(row), _canonical(row)))


def _version(record):
    return Decimal(str(record.get('version', '1')))


def find_evidence(report, records):
    """为一份报告选择最具体的有效比例凭据。"""
    records = list(records or [])
    exact = [row for row in records if row.get('report_id') == report.get('id')]
    if exact:
        candidates = exact
    else:
        candidates = [row for row in records
                      if not row.get('report_id')
                      and row.get('sample_id') == report.get('sample_id')
                      and (not row.get('product') or row.get('product') == report.get('product'))
                      and (not row.get('batch') or row.get('batch') == report.get('batch'))]
        if any(row.get('product') for row in candidates):
            candidates = [row for row in candidates if row.get('product')]
        if any(row.get('batch') for row in candidates):
            candidates = [row for row in candidates if row.get('batch')]
    if not candidates:
        return None
    newest = max(_version(row) for row in candidates)
    latest = [row for row in candidates if _version(row) == newest]
    if len({_canonical(row) for row in latest}) > 1:
        raise DomainError('EVIDENCE_CONFLICT', '同一报告存在互相矛盾的比例凭据')
    return sorted(latest, key=_canonical)[0]


def resolve_basis(report, records=None, legacy_default=False):
    """推导单份报告的可食部浓度依据。

    返回 resolved 或 pending；绝不使用批次级默认比例。
    """
    records = list(records or [])
    has_basis_column = 'basis' in report
    declared = normalize_basis(report.get('basis', '')) if has_basis_column else BASIS_UNKNOWN
    source_basis = _text(report.get('basis', '')) or BASIS_UNKNOWN
    evidence = find_evidence(report, records)
    # 报告自带可食部比例列时，把它当作随报告提交的凭据，同样逐份生效。
    inline = _decimal(report.get('edible_ratio'), 'EVIDENCE', '可食部比例无效')
    if inline is not None:
        if not (Decimal(0) < inline <= Decimal(1)):
            raise DomainError('EVIDENCE', '可食部比例必须大于0且不超过1')
        inline_record = {'evidence_id': 'REPORT-' + str(report.get('id', '')),
                         'basis': BASIS_WHOLE if inline < 1 else BASIS_EDIBLE,
                         'edible_ratio': str(inline)}
        if evidence and Decimal(str(evidence['edible_ratio'])) != inline:
            raise DomainError('EVIDENCE_CONFLICT', '报告比例与比例凭据冲突')
        evidence = inline_record

    def resolved(basis, ratio, converted):
        return {
            'status': 'resolved', 'basis': basis, 'edible_ratio': str(ratio),
            'concentration_factor': str(Decimal(1) / ratio),
            'source_basis': source_basis, 'evidence_id': evidence.get('evidence_id') if evidence else None,
            'converted': converted, 'legacy_default': False,
        }

    def pending(reason):
        return {
            'status': 'pending', 'reason': reason, 'basis': BASIS_UNKNOWN,
            'source_basis': source_basis,
            'evidence_id': evidence.get('evidence_id') if evidence else None,
        }

    if declared == BASIS_EDIBLE:
        if evidence and evidence.get('basis') != BASIS_EDIBLE:
            raise DomainError('BASIS_CONFLICT', '报告可食部基与比例凭据冲突')
        return resolved(BASIS_EDIBLE, Decimal(1), False)
    if declared == BASIS_WHOLE:
        if evidence and evidence.get('basis') != BASIS_WHOLE:
            raise DomainError('BASIS_CONFLICT', '报告全果基与比例凭据冲突')
        if not evidence:
            return pending('RATIO_MISSING')
        ratio = Decimal(str(evidence['edible_ratio']))
        return resolved(BASIS_WHOLE, ratio, True)
    if evidence:
        if evidence.get('basis') == BASIS_EDIBLE:
            return resolved(BASIS_EDIBLE, Decimal(1), False)
        ratio = Decimal(str(evidence['edible_ratio']))
        return resolved(BASIS_WHOLE, ratio, True)
    # 旧版报告根本没有“制备基准”列时，延续历史默认可食部基；列存在但为空或
    # 写“未注明”，则必须等待比例凭据，不能猜。
    if not has_basis_column and legacy_default:
        return {
            'status': 'resolved', 'basis': BASIS_EDIBLE, 'edible_ratio': '1',
            'concentration_factor': '1', 'source_basis': source_basis,
            'evidence_id': None, 'converted': False, 'legacy_default': True,
        }
    return pending('BASIS_MISSING')


def _source(raw, kind, encoding='utf-8'):
    return {
        'kind': kind, 'encoding': encoding,
        'sha256': hashlib.sha256(raw).hexdigest(),
        'content_b64': base64.b64encode(raw).decode('ascii'),
    }


def canonical_parameters(parameters):
    """把参数中的比例凭据归一成稳定结构，并保留凭据原文用于归档。

    同一来源重新运行时，凭据按版本合并、排序；重复凭据不会新增项目。
    """
    if parameters is None:
        return {}
    if not isinstance(parameters, dict):
        raise DomainError('PARAMETERS', '参数必须是对象')
    params = dict(parameters)
    records = list(params.get('ratios') or [])
    sources = list(params.get('supplement_sources') or [])
    if params.get('ratio_evidence') is not None:
        records.extend(params.get('ratio_evidence') or [])
    if params.get('ratio_csv') is not None:
        raw = params.get('ratio_csv')
        if isinstance(raw, str):
            raw = raw.encode('utf-8')
        text, encoding = decode_text(raw)
        parsed, _ = parse_ratio_csv(text, encoding)
        records.extend(parsed)
        sources.append(_source(raw, 'ratio_csv', encoding))
    if records:
        merged = merge_evidence([], records)
        if merged:
            params['ratios'] = merged
        # 原始凭据（即使只以 JSON 列表传入）也留档，防止证据数据丢失。
        json_raw = json.dumps(merged, ensure_ascii=False, sort_keys=True).encode('utf-8')
        sources.append(_source(json_raw, 'ratio_manifest'))
    params.pop('ratio_csv', None)
    params.pop('ratio_evidence', None)
    # 已留档的凭据原文随参数沿用，保证补录后的页面与导出仍能还原全部证据。
    for source in params.get('supplement_sources') or []:
        if isinstance(source, dict) and source.get('sha256'):
            sources.append(source)
    if sources:
        unique = {}
        for source in sources:
            if source.get('sha256'):
                unique[source['sha256']] = source
        params['supplement_sources'] = sorted(unique.values(), key=lambda item: item['sha256'])
    elif 'supplement_sources' in params:
        params.pop('supplement_sources', None)
    return params


def evidence_state(params):
    records = list(params.get('ratios') or [])
    return {
        'target_basis': TARGET_BASIS,
        'records': records,
        'sources': list(params.get('supplement_sources') or []),
        'count': len(records),
    }
