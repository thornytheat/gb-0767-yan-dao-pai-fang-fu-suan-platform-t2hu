from datetime import UTC, datetime
from hashlib import sha256

from app.archive.manifest import canonical
from app.errors import InputError
from app.monitor.inspection import Inspection


def trial_of(files, threshold_old, threshold_new):
    before, after = Inspection(files, threshold=threshold_old), Inspection(files, threshold=threshold_new)
    changes = []
    for old, new, old_rep, new_rep in zip(before.hours['rows'], after.hours['rows'],
                                          before.reports['rows'], after.reports['rows']):
        if old['status'] == new['status'] and old_rep['code'] == new_rep['code']:
            continue
        kind = 'recovered' if new['status'] == 'VALID' else 'demoted' if old['status'] == 'VALID' else 'shifted'
        changes.append({'hour': new['hour'], 'local': new['local'], 'valid': new['valid'], 'kind': kind,
                        'from': old['status'], 'to': new['status'], 'mg_nm3': new['mg_nm3'],
                        'report_from': old_rep['code'], 'report_to': new_rep['code'],
                        'reported_mg_nm3': new_rep['reported_mg_nm3'],
                        'difference_mg_nm3': new_rep['difference_mg_nm3']})
    return {'threshold_old': threshold_old, 'threshold_new': threshold_new,
            'hours': changes, 'recovered': sum(row['kind'] == 'recovered' for row in changes),
            'demoted': sum(row['kind'] == 'demoted' for row in changes),
            'deviations_old': before.reports['deviations'], 'deviations_new': after.reports['deviations']}


def propose(store, payload):
    if not isinstance(payload, dict) or set(payload) != {'run_id', 'threshold'}:
        raise InputError('BAD_PROPOSAL', '换规需要基准留档与新门槛')
    threshold = payload['threshold']
    if type(threshold) is not int or not 1 <= threshold <= 60:
        raise InputError('BAD_THRESHOLD', '新门槛须为 1–60 的整数')
    packet = store.require(str(payload['run_id']))
    stack = packet['manifest']['parameters']['stack']
    if store.latest_source_sha(stack) != packet['manifest']['sha256']:
        raise InputError('TRIAL_STALE', '基准留档的源数据已更新，请按最新留档试算', 409)
    effective = store.effective_rule(stack)
    current = effective['threshold'] if effective else packet['manifest']['parameters']['threshold']
    if threshold == current:
        raise InputError('SAME_THRESHOLD', '新门槛与现行门槛相同')
    epoch = store.approved_epoch(stack)
    record = {'id': sha256(canonical(['RULE-CHANGE', stack, threshold, packet['manifest']['run_id'], epoch])
                           .encode()).hexdigest(),
              'stack': stack, 'threshold': threshold, 'threshold_old': current,
              'base_run': packet['manifest']['run_id'], 'base_epoch': epoch,
              'trial': trial_of(packet['originals'], current, threshold), 'status': 'pending',
              'created_utc': datetime.now(UTC).isoformat(timespec='seconds'), 'approved_utc': None}
    return store.create_change(record)


def approve(store, change_id):
    return store.approve_change(change_id, datetime.now(UTC).isoformat(timespec='seconds'))
