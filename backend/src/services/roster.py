import hashlib
import secrets

import numpy as np
from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from src.models.roster import RosterEntry, RosterLock
from src.services import studies
from src.services.audits import log
from src.utils.serializers import now, record

MAX_SLOTS = 5000
MAX_SHIFTS = 100


def _rng(seed, shift_index, shift_name):
    """Stable per-shift stream so a stored seed reproduces the allocation."""
    digest = hashlib.sha256(f'roster-v1|{seed}|{shift_index}|{shift_name}'.encode('utf-8')).hexdigest()
    return np.random.default_rng(int(digest[:16], 16))


def allocate(seed, shifts):
    """Yield locked seats; each shift gets an equal, randomly placed AB/BA split."""
    for index, (name, slots) in enumerate(shifts):
        if len(slots) % 2:
            raise ValueError(f'班次 {name} 的纸样数必须为偶数，才能等额分派两种顺序')
        order = _rng(seed, index, name).permutation(len(slots))
        ab = {int(x) for x in order[:len(slots) // 2]}
        for position, slot in enumerate(slots, start=1):
            yield {
                'shift_index': index,
                'shift': name,
                'slot_key': slot,
                'position': position,
                'assigned_order': 'AB' if position - 1 in ab else 'BA',
            }


def _validated_shifts(data):
    if not data.shifts:
        raise HTTPException(422, '至少需要一个班次')
    if len(data.shifts) > MAX_SHIFTS:
        raise HTTPException(422, f'班次最多{MAX_SHIFTS}个')
    seen_names = set()
    seen_slots = set()
    shifts = []
    total = 0
    for item in data.shifts:
        name = item.name.strip()
        if not name:
            raise HTTPException(422, '班次名称不能为空')
        if name in seen_names:
            raise HTTPException(422, f'班次名称重复：{name}')
        seen_names.add(name)
        slots = [x.strip() for x in item.slots]
        if any(not x for x in slots):
            raise HTTPException(422, f'班次 {name} 存在空纸样编号')
        if len(slots) < 2:
            raise HTTPException(422, f'班次 {name} 至少需要2张纸样')
        if len(slots) % 2:
            raise HTTPException(422, f'班次 {name} 的纸样数必须为偶数，才能等额分派两种顺序')
        if len(set(slots)) != len(slots):
            raise HTTPException(422, f'班次 {name} 内纸样编号重复')
        for slot in slots:
            if slot in seen_slots:
                raise HTTPException(422, f'纸样编号跨班重复：{slot}')
            seen_slots.add(slot)
        total += len(slots)
        if total > MAX_SLOTS:
            raise HTTPException(422, f'名册最多{MAX_SLOTS}张纸样')
        shifts.append((name, slots))
    return shifts


def lock(db, study_id, data, user):
    studies.require(db, study_id)
    if db.scalar(select(RosterLock).where(RosterLock.study_id == study_id)):
        raise HTTPException(409, '名册已锁定，不可重抽或补抽')
    shifts = _validated_shifts(data)
    seed = (data.seed or '').strip() or secrets.token_hex(16)
    if len(seed) > 64:
        raise HTTPException(422, 'seed 最长64个字符')
    db.add(RosterLock(study_id=study_id, seed=seed, locked_at=now(), locked_by=user.email))
    for seat in allocate(seed, shifts):
        db.add(RosterEntry(study_id=study_id, **seat))
    log(db, user, 'roster.lock', f'{study_id}: {len(shifts)}班/{sum(len(s) for _, s in shifts)}席 seed={seed}')
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, '名册已锁定，不可重抽或补抽')
    return get(db, study_id)


def _entries(db, study_id):
    return list(db.scalars(select(RosterEntry).where(RosterEntry.study_id == study_id).order_by(RosterEntry.shift_index, RosterEntry.position)))


def _summary(entries):
    out = []
    for entry in entries:
        key = entry.shift_index
        if not out or out[-1]['shift_index'] != key:
            out.append({'shift_index': key, 'shift': entry.shift, 'seats': 0, 'ab': 0, 'ba': 0, 'claimed': 0, 'complete': 0, 'missing': 0})
        line = out[-1]
        line['seats'] += 1
        line['ab' if entry.assigned_order == 'AB' else 'ba'] += 1
        if entry.claimed_by:
            line['claimed'] += 1
        if entry.actual_order and entry.reading1 is not None and entry.reading2 is not None:
            line['complete'] += 1
        else:
            line['missing'] += 1
    return out


def get(db, study_id):
    studies.require(db, study_id)
    lock_row = db.scalar(select(RosterLock).where(RosterLock.study_id == study_id))
    entries = _entries(db, study_id) if lock_row else []
    return {
        'locked': bool(lock_row),
        'lock': record(lock_row) if lock_row else None,
        'entries': [record(x) for x in entries],
        'summary': _summary(entries),
    }


def _require_locked(db, study_id):
    lock_row = db.scalar(select(RosterLock).where(RosterLock.study_id == study_id))
    if not lock_row:
        raise HTTPException(409, '请先锁定纸样名册，再领单或排队')
    return lock_row


def _entry(db, study_id, entry_id):
    row = db.get(RosterEntry, entry_id)
    if not row or row.study_id != study_id:
        raise HTTPException(404, '名册席位不存在')
    return row


def claim(db, study_id, entry_id, worker, user):
    """Claim a seat once. Retries and concurrent claims read the same fixed order.

    The conditional UPDATE (claimed_by IS NULL) makes exactly one caller win; every
    other caller — including a retry of the same request — gets the stored seat
    back unchanged, so the assigned AB/BA order never changes.
    """
    _require_locked(db, study_id)
    row = _entry(db, study_id, entry_id)
    worker = (worker or '').strip() or user.email
    if len(worker) > 120:
        raise HTTPException(422, '领单人名称过长')
    created = False
    result = db.execute(
        update(RosterEntry)
        .where(RosterEntry.id == entry_id, RosterEntry.claimed_by.is_(None))
        .values(claimed_by=worker, claimed_at=now())
    )
    if result.rowcount:
        created = True
        log(db, user, 'roster.claim', f'{study_id}/{row.slot_key} order={row.assigned_order} worker={worker}')
    db.commit()
    db.refresh(row)
    payload = record(row)
    payload['newly_claimed'] = created
    return payload


def readings(db, study_id, entry_id, data, user):
    """Record the two readings once. A wrong actual order is refused outright."""
    _require_locked(db, study_id)
    row = _entry(db, study_id, entry_id)
    if not row.claimed_by:
        raise HTTPException(409, '请先领单；分派顺序锁定后才能登记读数')
    if data.actual_order != row.assigned_order:
        raise HTTPException(422, f'实际顺序 {data.actual_order} 与锁定分派 {row.assigned_order} 不符；错序拒绝登记和排队')
    values = (float(data.reading1), float(data.reading2))
    for value in values:
        if not np.isfinite(value) or value < 0 or value > 1e9:
            raise HTTPException(422, '读数必须为0到1e9之间的有限数值')
    if row.actual_order is not None:
        same = row.actual_order == data.actual_order and row.reading1 == values[0] and row.reading2 == values[1]
        if same:
            return record(row)  # idempotent retry of the same submission
        raise HTTPException(409, '该席位读数已登记；开测后不补抽，读数不可改写')
    result = db.execute(
        update(RosterEntry)
        .where(RosterEntry.id == entry_id, RosterEntry.actual_order.is_(None))
        .values(reading1=values[0], reading2=values[1], actual_order=data.actual_order, measured_by=user.email, measured_at=now())
    )
    if not result.rowcount:
        db.rollback()
        raise HTTPException(409, '该席位读数已登记；开测后不补抽，读数不可改写')
    log(db, user, 'roster.readings', f'{study_id}/{row.slot_key} order={data.actual_order}')
    db.commit()
    db.refresh(row)
    return record(row)


def frozen_snapshot(db, study_id):
    """Freeze the locked roster and every reading exactly as they are at queue time."""
    lock_row = _require_locked(db, study_id)
    query = select(RosterEntry).where(RosterEntry.study_id == study_id).order_by(RosterEntry.shift_index, RosterEntry.position)
    dialect = db.get_bind().dialect.name
    if dialect != 'sqlite':
        query = query.with_for_update()
    entries = list(db.scalars(query))
    bias = [x.slot_key for x in entries if x.actual_order and x.actual_order != x.assigned_order]
    if bias:
        raise HTTPException(422, '实际顺序与锁定分派不符，错序拒绝排队：' + ', '.join(bias[:10]))
    partial = []
    for row in entries:
        has = (row.reading1 is not None, row.reading2 is not None, row.actual_order is not None)
        if any(has) and not all(has):
            partial.append(row.slot_key)
    if partial:
        raise HTTPException(422, '读数不完整，拒绝排队：' + ', '.join(partial[:10]))

    shifts = []
    for row in entries:
        if not shifts or shifts[-1]['shift_index'] != row.shift_index:
            shifts.append({'shift_index': row.shift_index, 'shift': row.shift, 'seats': 0, 'quota_ab': 0, 'quota_ba': 0, 'entries': []})
        group = shifts[-1]
        group['seats'] += 1
        if row.assigned_order == 'AB':
            group['quota_ab'] += 1
        else:
            group['quota_ba'] += 1
        group['entries'].append({
            'slot_key': row.slot_key,
            'position': row.position,
            'assigned_order': row.assigned_order,
            'actual_order': row.actual_order,
            'reading1': row.reading1,
            'reading2': row.reading2,
        })
    for group in shifts:
        if group['quota_ab'] != group['quota_ba'] or group['seats'] % 2:
            raise HTTPException(422, f"班次 {group['shift']} 的分派配额不等额，拒绝排队")
    complete=sum(1 for group in shifts for entry in group['entries'] if entry['actual_order'])
    if complete<2:
        raise HTTPException(422, f'完整读数仅{complete}对，至少需要2对才能排队；缺测纸样会保留席位，但请先登记足够读数')
    return {
        'roster': {
            'seed': lock_row.seed,
            'locked_at': lock_row.locked_at,
            'locked_by': lock_row.locked_by,
            'algorithm': 'shift-block-AB-BA-v1',
        },
        'shifts': shifts,
    }


def slots_for_export(db, study_id):
    return [record(x) for x in _entries(db, study_id)]
