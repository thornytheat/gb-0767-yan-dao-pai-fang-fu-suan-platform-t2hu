from flask import Blueprint, current_app, jsonify, request

from app.archive.certificate import issue
from app.archive.rulechange import approve, propose
from app.archive.run import archive_run, governed_inspection
from app.errors import InputError
from app.monitor.inspection import STAGES
from app.monitor.source import sample

api = Blueprint('api', __name__, url_prefix='/api')


def archive():
    return current_app.extensions['monitor_archive']


@api.get('/samples/<name>/<stage>')
def preview(name, stage):
    if stage not in STAGES:
        raise InputError('STAGE_UNKNOWN', '找不到监测工序', 404)
    audit, _ = governed_inspection(archive(), sample(name))
    return jsonify(getattr(audit, stage))


@api.post('/runs')
def create_run():
    packet, reused = archive_run(archive(), request.get_json(silent=True))
    return jsonify(run_id=packet['manifest']['run_id'], reused=reused, manifest=packet['manifest'],
                   totals=packet['results']['hours']['totals'], deviations=packet['results']['reports']['deviations'])


@api.get('/runs')
def runs():
    return jsonify(runs=archive().history())


@api.get('/runs/<run_id>/<stage>')
def snapshot(run_id, stage):
    if stage not in STAGES:
        raise InputError('STAGE_UNKNOWN', '找不到监测工序', 404)
    return jsonify(archive().require(run_id)['results'][stage])


@api.get('/export/<run_id>')
def export(run_id):
    response = jsonify(issue(archive().require(run_id)))
    response.headers['Content-Disposition'] = f'attachment; filename="monitor-{run_id}.json"'
    return response


@api.post('/rule-changes')
def create_rule_change():
    change, reused = propose(archive(), request.get_json(silent=True))
    return jsonify(change=change, reused=reused)


@api.get('/rule-changes')
def rule_changes():
    return jsonify(changes=archive().list_changes(), effective=archive().effective_rules())


@api.post('/rule-changes/<change_id>/approve')
def approve_rule_change(change_id):
    change, reused = approve(archive(), change_id)
    return jsonify(change=change, reused=reused)
