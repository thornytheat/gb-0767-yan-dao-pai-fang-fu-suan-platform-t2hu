from time import perf_counter

from app.archive.manifest import batch_key, manifest
from app.monitor.inspection import Inspection
from app.monitor.source import input_packet, window_of


def governed_inspection(store, files):
    rule = store.effective_rule(window_of(files).stack)
    return Inspection(files, threshold=None if rule is None else rule['threshold']), rule


def archive_run(store, payload):
    started = perf_counter()
    files, name = input_packet(payload)
    audit, rule = governed_inspection(store, files)
    run_id, hashes = batch_key(files, audit.window.threshold)
    record = store.lookup(run_id)
    if record is not None:
        return record, True
    results = audit.results()
    packet = {'manifest': manifest(run_id, hashes, vars(audit.window), name, started,
                                   None if rule is None else rule['id']),
              'originals': files, 'results': results}
    return store.commit(run_id, packet)
