from concurrent.futures import ThreadPoolExecutor

from app.main import create_app
from app.monitor.source import sample


def archived(client, payload={'sample': 'dirty'}):
    return client.post('/api/runs', json=payload).json['run_id']


def trial(client, run_id, threshold):
    return client.post('/api/rule-changes', json={'run_id': run_id, 'threshold': threshold})


def test_trial_points_out_recovered_hours(client):
    run_id = archived(client)
    response = trial(client, run_id, 44)
    assert response.status_code == 200 and response.json['reused'] is False
    change = response.json['change']
    assert (change['status'], change['threshold_old'], change['threshold']) == ('pending', 45, 44)
    [hour] = [row for row in change['trial']['hours'] if row['kind'] == 'recovered']
    assert (hour['local'], hour['valid'], hour['from'], hour['to']) == ('11-14 09:00', 44, 'INSUFFICIENT', 'VALID')
    assert (hour['mg_nm3'], hour['report_from'], hour['report_to']) == ('30.00', 'NO_REFERENCE', 'DEVIATION')
    assert (change['trial']['recovered'], change['trial']['demoted']) == (1, 0)


def test_raised_threshold_demotes_hours(client):
    run_id = archived(client)
    change = trial(client, run_id, 48).json['change']
    [hour] = [row for row in change['trial']['hours'] if row['kind'] == 'demoted']
    assert (hour['from'], hour['to'], hour['mg_nm3']) == ('VALID', 'INSUFFICIENT', None)
    assert change['trial']['demoted'] == 1


def test_same_trial_is_reused(client):
    run_id = archived(client)
    first, second = trial(client, run_id, 44).json, trial(client, run_id, 44).json
    assert (first['reused'], second['reused']) == (False, True)
    assert first['change']['id'] == second['change']['id']


def test_bad_proposals_rejected(client):
    run_id = archived(client)
    assert trial(client, run_id, 0).json['code'] == 'BAD_THRESHOLD'
    assert trial(client, run_id, 61).json['code'] == 'BAD_THRESHOLD'
    assert trial(client, run_id, '44').json['code'] == 'BAD_THRESHOLD'
    assert trial(client, run_id, True).json['code'] == 'BAD_THRESHOLD'
    assert trial(client, run_id, 45).json['code'] == 'SAME_THRESHOLD'
    assert trial(client, 'not-archived', 44).status_code == 404
    assert client.post('/api/rule-changes', json={'run_id': run_id}).json['code'] == 'BAD_PROPOSAL'


def test_approve_switches_threshold_for_new_runs_only(client):
    run_id = archived(client)
    change_id = trial(client, run_id, 44).json['change']['id']
    approved = client.post(f'/api/rule-changes/{change_id}/approve')
    assert approved.status_code == 200 and approved.json['change']['status'] == 'approved'
    assert client.post(f'/api/rule-changes/{change_id}/approve').json['reused'] is True
    new = client.post('/api/runs', json={'sample': 'dirty'}).json
    assert new['run_id'] != run_id
    assert new['manifest']['parameters']['threshold'] == 44
    assert new['manifest']['rule_change'] == change_id
    rows = client.get(f"/api/runs/{new['run_id']}/hours").json['rows']
    assert (rows[1]['status'], rows[1]['mg_nm3'], rows[1]['threshold']) == ('VALID', '30.00', 44)
    old = client.get(f'/api/runs/{run_id}/hours').json['rows']
    assert (old[1]['status'], old[1]['mg_nm3'], old[1]['threshold']) == ('INSUFFICIENT', None, 45)
    assert client.get(f'/api/runs/{run_id}/reports').json['rows'][1]['code'] == 'NO_REFERENCE'


def test_updated_source_data_invalidates_trial(client):
    run_id = archived(client)
    change_id = trial(client, run_id, 44).json['change']['id']
    files = sample('dirty')
    archived(client, {'files': files | {'reports': files['reports'] + '\r\n'}})
    response = client.post(f'/api/rule-changes/{change_id}/approve')
    assert (response.status_code, response.json['code']) == (409, 'TRIAL_STALE')
    assert trial(client, run_id, 40).status_code == 409


def test_only_one_threshold_set_is_approved(client):
    run_id = archived(client)
    first = trial(client, run_id, 44).json['change']['id']
    second = trial(client, run_id, 40).json['change']['id']
    assert client.post(f'/api/rule-changes/{first}/approve').status_code == 200
    response = client.post(f'/api/rule-changes/{second}/approve')
    assert (response.status_code, response.json['code']) == (409, 'RULE_CONFLICT')


def test_concurrent_shifts_single_winner(tmp_path):
    path = tmp_path / 'race.sqlite'
    clients = [create_app(path).test_client() for _ in range(2)]
    run_id = archived(clients[0])
    change_ids = [trial(clients[i % 2], run_id, threshold).json['change']['id']
                  for i, threshold in enumerate((44, 40))]
    with ThreadPoolExecutor(2) as pool:
        outcomes = list(pool.map(lambda i: clients[i].post(
            f'/api/rule-changes/{change_ids[i]}/approve').status_code, range(2)))
    assert sorted(outcomes) == [200, 409]
    assert clients[0].get('/api/rule-changes').json['effective']['K-04']['threshold'] in (40, 44)


def test_repropose_after_competing_approval_gets_fresh_trial(client):
    run_id = archived(client)
    stale_id = trial(client, run_id, 44).json['change']['id']
    other = trial(client, run_id, 40).json['change']['id']
    assert client.post(f'/api/rule-changes/{other}/approve').status_code == 200
    assert client.post(f'/api/rule-changes/{stale_id}/approve').status_code == 409
    fresh = trial(client, run_id, 44).json
    assert fresh['reused'] is False and fresh['change']['id'] != stale_id
    assert fresh['change']['trial']['threshold_old'] == 40
    assert client.post(f"/api/rule-changes/{fresh['change']['id']}/approve").status_code == 200


def test_unknown_change_not_approvable(client):
    assert client.post('/api/rule-changes/not-a-change/approve').status_code == 404


def test_sequential_rule_changes(client):
    first_run = archived(client)
    first = trial(client, first_run, 44).json['change']['id']
    assert client.post(f'/api/rule-changes/{first}/approve').status_code == 200
    second_run = archived(client)
    assert second_run != first_run
    second = trial(client, second_run, 45).json['change']
    assert (second['threshold_old'], second['base_epoch']) == (44, 1)
    assert client.post(f"/api/rule-changes/{second['id']}/approve").status_code == 200
    latest = client.post('/api/runs', json={'sample': 'dirty'}).json
    assert latest['manifest']['parameters']['threshold'] == 45
    assert latest['reused'] is True and latest['run_id'] == first_run
