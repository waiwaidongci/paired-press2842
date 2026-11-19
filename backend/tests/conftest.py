import os
import tempfile

import pytest

_DB = tempfile.NamedTemporaryFile(suffix='.sqlite3', delete=False).name
os.environ.setdefault('DATABASE_URL', f'sqlite+pysqlite:///{_DB}')
os.environ.setdefault('REDIS_URL', 'redis://127.0.0.1:1/0')
os.environ.setdefault('JWT_SECRET', 'test-secret')
os.environ.pop('SEED_DEMO', None)

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select  # noqa: E402

from src.config.database import Base, Session, engine  # noqa: E402
from src.main import app  # noqa: E402
from src.models.user import User  # noqa: E402
from src.utils.security import hash_password  # noqa: E402


class FakeRedis:
    def __init__(self):
        self.counters = {}

    def incr(self, key):
        self.counters[key] = self.counters.get(key, 0) + 1
        return self.counters[key]

    def expire(self, key, ttl):
        return True


@pytest.fixture(scope='session', autouse=True)
def schema():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with Session() as db:
        for role in ('admin', 'analyst', 'viewer'):
            db.add(User(email=f'{role}@test.local', role=role, password_hash=hash_password('pw-123456')))
        db.commit()
    yield
    Base.metadata.drop_all(engine)


@pytest.fixture()
def client(monkeypatch):
    import src.middlewares.rate_limit as rate_limit
    import src.services.runs as runs_service

    fake = FakeRedis()
    monkeypatch.setattr(rate_limit, 'redis', fake)
    queued = []
    monkeypatch.setattr(runs_service, 'enqueue', lambda run_id: queued.append(run_id))
    with TestClient(app) as c:
        c.queued = queued
        yield c


def login(client, role='admin'):
    r = client.post('/auth/login', json={'email': f'{role}@test.local', 'password': 'pw-123456'})
    assert r.status_code == 200, r.text
    return {'Authorization': 'Bearer ' + r.json()['token']}


def make_study(client, headers, name):
    r = client.post('/studies', json={'name': name, 'source_filename': 'roster.csv', 'description': ''}, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()['id']


_counter = {'n': 0}


def unique_name(prefix):
    _counter['n'] += 1
    return f'{prefix}-{_counter["n"]}'


def lock_roster(client, headers, study_id, shifts, seed='unit-seed'):
    payload = {'seed': seed, 'shifts': [{'name': n, 'slots': s} for n, s in shifts]}
    r = client.post(f'/studies/{study_id}/roster/lock', json=payload, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def seat_ids(roster):
    return [e['id'] for e in roster['entries']]
