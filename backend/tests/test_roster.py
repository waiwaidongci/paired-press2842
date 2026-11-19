import itertools
import math

import pytest

from src.services import algorithm

from conftest import login, lock_roster, make_study, seat_ids, unique_name


def _snapshot(shifts, missing=(), seed='s'):
    """Build a frozen snapshot; values chosen so AB/BA order changes the sign."""
    out = {'roster': {'seed': seed}, 'shifts': []}
    for index, (name, slots) in enumerate(shifts):
        entries = []
        for position, slot in enumerate(slots, start=1):
            order = 'AB' if position % 2 else 'BA'
            values = missing.get(slot) if isinstance(missing, dict) else None
            entry = {'slot_key': slot, 'position': position, 'assigned_order': order, 'actual_order': order,
                     'reading1': None, 'reading2': None, 'quota_ab': None}
            if values is None:
                entry['reading1'], entry['reading2'] = values_ab = values_by_order(order, position)
            entries.append(entry)
        out['shifts'].append({'shift_index': index, 'shift': name, 'seats': len(slots),
                              'quota_ab': len(slots) // 2, 'quota_ba': len(slots) - len(slots) // 2, 'entries': entries})
    return out


def values_by_order(order, position):
    # A-ish reading 5.0, B-ish reading 5.0 + delta; order decides which reading slot holds which.
    delta = 0.2 + 0.05 * position
    if order == 'AB':
        return (5.0, 5.0 + delta)
    return (5.0 + delta, 5.0)


def _build(shift_sizes, missing_slots=()):
    shifts = []
    counter = 1
    for s, size in enumerate(shift_sizes):
        slots = []
        for _ in range(size):
            slots.append(f'P{counter:03d}')
            counter += 1
        shifts.append((f'班次{s+1}', slots))
    snapshot = {'roster': {'seed': 'demo'}, 'shifts': []}
    for index, (name, slots) in enumerate(shifts):
        entries = []
        for position, slot in enumerate(slots, start=1):
            order = 'AB' if position % 2 else 'BA'
            entry = {'slot_key': slot, 'position': position, 'assigned_order': order, 'actual_order': order}
            if slot in missing_slots:
                entry.update(reading1=None, reading2=None, actual_order=None)
            else:
                r1, r2 = values_by_order(order, position)
                entry.update(reading1=r1, reading2=r2)
            entries.append(entry)
        snapshot['shifts'].append({'shift_index': index, 'shift': name, 'seats': len(slots),
                                   'quota_ab': len(slots) // 2, 'quota_ba': len(slots) // 2, 'entries': entries})
    return snapshot


def test_extract_swaps_reading_order_to_before_after():
    snap = _build([4])
    shifts, missing = algorithm._extract(snap)
    assert not missing
    pairs = shifts[0]['complete']
    for pair in pairs:
        assert pair['difference'] > 0  # B is always higher than A
        assert pair['pair_difference'] * pair['signed'] > 0


def test_block_exact_never_uses_impossible_orders():
    """The exact test must only consider sign patterns allowed by the sealed quota."""
    snap = _build([4])
    shifts, _ = algorithm._extract(snap)
    blocks, _, _ = algorithm._blocks(shifts)
    block = blocks[0]
    patterns = algorithm._exact_block(block)
    for signs, multiplicity in patterns:
        assert int((signs > 0).sum()) == block['quota_ab']
        assert multiplicity == 1
    assert len(patterns) == math.comb(4, 2)


def test_missing_seats_stay_in_roster_and_widen_sign_patterns():
    snap = _build([6], missing_slots={'P002', 'P005'})
    shifts, missing = algorithm._extract(snap)
    assert len(missing) == 2
    blocks, _, n = algorithm._blocks(shifts)
    assert n == 4 and blocks[0]['seats'] == 6 and blocks[0]['missing'] == 2
    patterns = algorithm._exact_block(blocks[0])
    # Quota is 3 AB of 6 seats, 2 seats missing: complete-seat AB counts 1..3 are possible.
    assert {int((s > 0).sum()) for s, _ in patterns} == {1, 2, 3}
    assert sum(w for _, w in patterns) == math.comb(6, 3)


def test_all_positive_differences_give_smallest_p():
    snap = _build([4, 4])
    result = algorithm.calculate_roster(snap, {'resamples': 10000})
    # All differences positive: only all-plus and all-minus reach |observed|.
    # Each shift has C(4,2)=6 legal assignments; free per-paper flipping (C(8,4)=70, p=2/70)
    # would wrongly count orders the design cannot produce.
    expected = 2 / (math.comb(4, 2) ** 2)
    assert result['metrics']['双侧符号翻转p'] == pytest.approx(expected)
    assert result['metrics']['完整配对样本量'] == 8


def test_swapping_columns_keeps_p_and_flips_sign():
    snap = _build([4, 4])
    flipped = _build([4, 4])
    for group in flipped['shifts']:
        for entry in group['entries']:
            entry['reading1'], entry['reading2'] = entry['reading2'], entry['reading1']
    a = algorithm.calculate_roster(snap, {})
    b = algorithm.calculate_roster(flipped, {})
    assert a['metrics']['双侧符号翻转p'] == pytest.approx(b['metrics']['双侧符号翻转p'])
    assert a['metrics']['平均差(新减原)'] == pytest.approx(-b['metrics']['平均差(新减原)'])


def test_missing_values_do_not_change_missing_seat_quota():
    snap = _build([6], missing_slots={'P001', 'P003'})
    result = algorithm.calculate_roster(snap, {})
    blocks, _, _ = algorithm._blocks(algorithm._extract(snap)[0])
    assert result['metrics']['名册席位数'] == 6
    assert result['metrics']['缺测席位数'] == 2
    assert result['test_basis']['班次配额与依据'][0]['缺测席位'] == 2


def test_mc_path_is_reproducible_and_conservative():
    snap = _build([12, 12], missing_slots={'P003'})
    a = algorithm.calculate_roster(snap, {'resamples': 300, 'seed': 7})
    b = algorithm.calculate_roster(snap, {'resamples': 300, 'seed': 7})
    assert a['metrics']['双侧符号翻转p'] == b['metrics']['双侧符号翻转p']
    assert a['metrics']['计算方法'].endswith('Monte Carlo 加一校正')


@pytest.fixture()
def study(client):
    headers = login(client)
    study_id = make_study(client, headers, unique_name('roster'))
    return headers, study_id


def test_lock_requires_even_shift_and_no_retry(client, study):
    headers, study_id = study
    r = client.post(f'/studies/{study_id}/roster/lock', json={'shifts': [{'name': 'A班', 'slots': ['X1', 'X2', 'X3']}]}, headers=headers)
    assert r.status_code == 422 and '偶数' in r.json()['detail']
    roster = lock_roster(client, headers, study_id, [('A班', ['X1', 'X2', 'X3', 'X4'])])
    assert roster['locked'] and roster['summary'][0]['ab'] == roster['summary'][0]['ba'] == 2
    # No re-draw, no supplemental draws.
    again = client.post(f'/studies/{study_id}/roster/lock', json={'shifts': [{'name': 'A班', 'slots': ['Y1', 'Y2']}]}, headers=headers)
    assert again.status_code == 409


def test_concurrent_claim_returns_same_order_and_never_changes_it(client, study):
    headers, study_id = study
    roster = lock_roster(client, headers, study_id, [('A班', ['K1', 'K2', 'K3', 'K4'])])
    entry = roster['entries'][0]
    first = client.post(f'/studies/{study_id}/roster/{entry["id"]}/claim', json={'worker': 'w1'}, headers=headers)
    second = client.post(f'/studies/{study_id}/roster/{entry["id"]}/claim', json={'worker': 'w2'}, headers=headers)
    assert first.status_code == second.status_code == 200
    assert first.json()['assigned_order'] == second.json()['assigned_order'] == entry['assigned_order']
    assert first.json()['newly_claimed'] is True and second.json()['newly_claimed'] is False


def test_wrong_actual_order_refused_and_missing_seats_keep_their_place(client, study):
    headers, study_id = study
    roster = lock_roster(client, headers, study_id, [('A班', ['W1', 'W2', 'W3', 'W4'])])
    entry = roster['entries'][0]
    client.post(f'/studies/{study_id}/roster/{entry["id"]}/claim', json={}, headers=headers)
    wrong = 'BA' if entry['assigned_order'] == 'AB' else 'AB'
    r = client.post(f'/studies/{study_id}/roster/{entry["id"]}/readings', json={'reading1': 1.0, 'reading2': 2.0, 'actual_order': wrong}, headers=headers)
    assert r.status_code == 422 and '错序' in r.json()['detail']
    ok = client.post(f'/studies/{study_id}/roster/{entry["id"]}/readings', json={'reading1': 1.0, 'reading2': 2.0, 'actual_order': entry['assigned_order']}, headers=headers)
    assert ok.status_code == 200
    dup = client.post(f'/studies/{study_id}/roster/{entry["id"]}/readings', json={'reading1': 9.0, 'reading2': 8.0, 'actual_order': entry['assigned_order']}, headers=headers)
    assert dup.status_code == 409
    # With only one measured pair the queue guard stops the run.
    too_few = client.post('/runs', json={'study_id': study_id, 'name': 'too-few', 'parameters': {}}, headers=headers)
    assert too_few.status_code == 422 and '至少需要2对' in too_few.json()['detail']
    # Measure one more seat; the two unmeasured seats still keep their roster positions.
    second = roster['entries'][2]
    client.post(f'/studies/{study_id}/roster/{second["id"]}/claim', json={}, headers=headers)
    client.post(f'/studies/{study_id}/roster/{second["id"]}/readings',
                json={'reading1': 1.1, 'reading2': 1.7, 'actual_order': second['assigned_order']}, headers=headers)
    run = client.post('/runs', json={'study_id': study_id, 'name': 'missing', 'parameters': {}}, headers=headers)
    assert run.status_code == 200, run.text
    frozen = client.get(f'/runs/{run.json()["id"]}', headers=headers).json()
    assert frozen['snapshot']['shifts'][0]['seats'] == 4
    missing = [e for e in frozen['snapshot']['shifts'][0]['entries'] if e['actual_order'] is None]
    assert len(missing) == 2


def test_queue_freezes_snapshot_and_later_edits_do_not_change_it(client, study):
    headers, study_id = study
    roster = lock_roster(client, headers, study_id, [('A班', ['Q1', 'Q2', 'Q3', 'Q4'])])
    ids = seat_ids(roster)
    for entry_id in ids:
        entry = next(x for x in roster['entries'] if x['id'] == entry_id)
        client.post(f'/studies/{study_id}/roster/{entry_id}/claim', json={}, headers=headers)
        client.post(f'/studies/{study_id}/roster/{entry_id}/readings', json={'reading1': 1.0, 'reading2': 1.5, 'actual_order': entry['assigned_order']}, headers=headers)
    run = client.post('/runs', json={'study_id': study_id, 'name': 'full', 'parameters': {}}, headers=headers)
    assert run.status_code == 200, run.text
    frozen = client.get(f'/runs/{run.json()["id"]}', headers=headers).json()
    assert frozen['snapshot']['shifts'][0]['seats'] == 4
    assert len(frozen['snapshot']['shifts'][0]['entries']) == 4
    assert client.queued[-1] == run.json()['id']
    # A later reading attempt cannot rewrite the frozen sample.
    retry = client.post(f'/studies/{study_id}/roster/{ids[0]}/readings', json={'reading1': 7.0, 'reading2': 8.0, 'actual_order': roster['entries'][0]['assigned_order']}, headers=headers)
    assert retry.status_code == 409
    after = client.get(f'/runs/{run.json()["id"]}', headers=headers).json()
    assert after['snapshot'] == frozen['snapshot']


def test_export_contains_test_basis(client, study):
    headers, study_id = study
    roster = lock_roster(client, headers, study_id, [('A班', ['E1', 'E2', 'E3', 'E4'])])
    for entry in roster['entries']:
        client.post(f'/studies/{study_id}/roster/{entry["id"]}/claim', json={}, headers=headers)
        client.post(f'/studies/{study_id}/roster/{entry["id"]}/readings', json={'reading1': 1.0, 'reading2': 1.6, 'actual_order': entry['assigned_order']}, headers=headers)
    run = client.post('/runs', json={'study_id': study_id, 'name': 'export', 'parameters': {}}, headers=headers).json()
    # Run the worker calculation directly for the frozen snapshot.
    from src.config.database import Session
    from src.models.run import Run as RunModel
    from src.services.algorithm import calculate
    from src.utils.serializers import checksum, now
    with Session() as db:
        row = db.get(RunModel, run['id'])
        row.result = calculate(row.snapshot, row.parameters)
        row.result['input_sha256'] = row.checksum
        row.status = 'completed'
        db.commit()
    csv_response = client.get(f'/runs/{run["id"]}/export', headers=headers)
    assert csv_response.status_code == 200
    text = csv_response.content.decode('utf-8-sig')
    assert '检验依据' in text and '随机种子' in text and '班次配额与依据' in text


def test_independent_oracle_enumerates_every_legal_seat_assignment():
    """Independent brute force: enumerate all C(seats, quota_AB) seat assignments directly."""
    snap = _build([4, 6], missing_slots={'P006', 'P007', 'P008'})
    shifts, _ = algorithm._extract(snap)
    result = algorithm.calculate_roster(snap, {})
    observed = result['metrics']['平均差(新减原)']
    n_complete = result['metrics']['完整配对样本量']
    assert n_complete == 7

    total = 0
    extreme = 0
    shift_stats = []
    for group, block in zip(shifts, algorithm._blocks(shifts)[0]):
        seats = block['seats']
        quota = block['quota_ab']
        diffs = [None] * seats
        for item in group['complete']:
            diffs[item['seat_index']] = item['pair_difference']
        outcomes = []
        for chosen in itertools.combinations(range(seats), quota):
            chosen = set(chosen)
            stat = 0.0
            for index, value in enumerate(diffs):
                if value is None:
                    continue
                stat += value if index in chosen else -value
            outcomes.append(stat)
        shift_stats.append(outcomes)

    for combo in itertools.product(*shift_stats):
        total += 1
        if abs(sum(combo) / n_complete) >= abs(observed) - 1e-12:
            extreme += 1
    assert total == math.comb(4, 2) * math.comb(6, 3)
    assert result['metrics']['双侧符号翻转p'] == pytest.approx(extreme / total)


def test_late_readings_before_queue_are_included_after_queue_not(client):
    headers = login(client)
    study_id = make_study(client, headers, unique_name('late'))
    lock_roster(client, headers, study_id, [('A班', ['L1', 'L2', 'L3', 'L4'])])
    roster = client.get(f'/studies/{study_id}/roster', headers=headers).json()

    def record(entry, value):
        client.post(f'/studies/{study_id}/roster/{entry["id"]}/claim', json={}, headers=headers)
        return client.post(f'/studies/{study_id}/roster/{entry["id"]}/readings',
                           json={'reading1': value, 'reading2': value + 0.5, 'actual_order': entry['assigned_order']}, headers=headers)

    record(roster['entries'][0], 1.0)
    # Late top-up reading arrives before queueing and is frozen in.
    assert record(roster['entries'][1], 1.2).status_code == 200
    run = client.post('/runs', json={'study_id': study_id, 'name': 'late', 'parameters': {}}, headers=headers)
    assert run.status_code == 200
    frozen = client.get(f'/runs/{run.json()["id"]}', headers=headers).json()
    done = [e for e in frozen['snapshot']['shifts'][0]['entries'] if e['actual_order']]
    assert len(done) == 2
    assert done[1]['reading1'] == 1.2
    # After queueing, a top-up for a still-missing seat must not enter the frozen snapshot.
    assert record(roster['entries'][2], 2.0).status_code == 200
    assert client.get(f'/runs/{run.json()["id"]}', headers=headers).json()['snapshot'] == frozen['snapshot']
    assert len([e for e in frozen['snapshot']['shifts'][0]['entries'] if e['actual_order'] is None]) == 2


def test_parallel_claims_agree_and_only_one_wins(client, study):
    from concurrent.futures import ThreadPoolExecutor
    headers, study_id = study
    roster = lock_roster(client, headers, study_id, [('A班', ['C1', 'C2', 'C3', 'C4'])])
    entry = roster['entries'][0]

    def claim(name):
        return client.post(f'/studies/{study_id}/roster/{entry["id"]}/claim', json={'worker': name}, headers=headers)

    with ThreadPoolExecutor(max_workers=8) as pool:
        responses = list(pool.map(claim, [f'w{i}' for i in range(8)]))
    assert all(r.status_code == 200 for r in responses)
    assert {r.json()['assigned_order'] for r in responses} == {entry['assigned_order']}
    assert sum(1 for r in responses if r.json()['newly_claimed']) == 1
    winner = sorted(r.json()['claimed_by'] for r in responses if r.json()['newly_claimed'])[0]
    final = client.get(f'/studies/{study_id}/roster', headers=headers).json()
    stored = next(x for x in final['entries'] if x['id'] == entry['id'])
    assert stored['claimed_by'] == winner


def test_oracle_agrees_with_unequal_deltas_and_two_shifts():
    """Seat-level brute force with distinct magnitudes, two shifts, mixed missing counts."""
    snap = _build([6, 6], missing_slots={'P002', 'P005', 'P009'})
    for gi, group in enumerate(snap['shifts']):
        for ei, entry in enumerate(group['entries']):
            if entry['actual_order']:
                base = 0.1 + 0.13 * ei + 0.7 * gi
                if entry['assigned_order'] == 'AB':
                    entry['reading1'], entry['reading2'] = 5.0, 5.0 + base
                else:
                    entry['reading1'], entry['reading2'] = 5.0 + base, 5.0

    result = algorithm.calculate_roster(snap, {})
    observed = result['metrics']['平均差(新减原)']
    n = result['metrics']['完整配对样本量']
    shifts, _ = algorithm._extract(snap)
    blocks, _, _ = algorithm._blocks(shifts)

    per_shift = []
    for group, block in zip(shifts, blocks):
        seats, quota = block['seats'], block['quota_ab']
        pair_by_seat = {it['seat_index']: it['pair_difference'] for it in group['complete']}
        outcomes = []
        for chosen in itertools.combinations(range(seats), quota):
            chosen = set(chosen)
            outcomes.append(sum(v if i in chosen else -v for i, v in pair_by_seat.items()))
        per_shift.append(outcomes)

    total = extreme = 0
    for combo in itertools.product(*per_shift):
        total += 1
        if abs(sum(combo) / n) >= abs(observed) - 1e-12:
            extreme += 1
    assert total == math.comb(6, 3) * math.comb(6, 3)
    assert result['metrics']['双侧符号翻转p'] == pytest.approx(extreme / total)


def test_many_missing_seats_keep_exact_weights_exact():
    """40 seats but only 16 measured: seat-level weights are huge; p must stay exact."""
    entries = []
    for pos in range(1, 41):
        order = 'AB' if pos % 2 else 'BA'
        entry = {'slot_key': f'P{pos:03d}', 'position': pos, 'assigned_order': order, 'actual_order': order}
        if pos <= 16:
            d = 0.2 + 0.01 * pos
            entry['reading1'], entry['reading2'] = (5.0, 5.0 + d) if order == 'AB' else (5.0 + d, 5.0)
        else:
            entry.update(reading1=None, reading2=None, actual_order=None)
        entries.append(entry)
    snap = {'roster': {'seed': 's'}, 'shifts': [{'shift_index': 0, 'shift': 'S', 'seats': 40,
                                                  'quota_ab': 20, 'quota_ba': 20, 'entries': entries}]}
    result = algorithm.calculate_roster(snap, {})
    assert result['metrics']['完整配对样本量'] == 16
    assert result['metrics']['缺测席位数'] == 24
    # Max |statistic| only when every measured seat keeps its +d sign, in either direction;
    # the 24 missing seats absorb the other 12 AB assignments.
    expected = 2 * math.comb(24, 12) / math.comb(40, 20)
    assert result['metrics']['双侧符号翻转p'] == pytest.approx(expected, rel=1e-12)
