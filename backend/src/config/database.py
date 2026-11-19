import os
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker
class Base(DeclarativeBase):
    pass
_url=os.environ['DATABASE_URL']
_engine_kwargs={'pool_pre_ping':True}
if _url.startswith('sqlite'):
    # Test and local file databases are used from multiple threads by the API/test client.
    _engine_kwargs['connect_args']={'check_same_thread':False}
engine = create_engine(_url, **_engine_kwargs)
Session = sessionmaker(bind=engine, expire_on_commit=False)
def get_db():
    with Session() as db:
        yield db
