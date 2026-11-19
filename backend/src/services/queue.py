import os
from redis import Redis


def enqueue(run_id, queue='analysis-queue'):
    """Push a frozen run to the worker queue. Importable so tests can stub it."""
    Redis.from_url(os.environ['REDIS_URL']).rpush(queue,run_id)
