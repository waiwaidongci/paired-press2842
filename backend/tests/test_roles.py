import os,tempfile
db=tempfile.NamedTemporaryFile(suffix='.sqlite3',delete=False).name
os.environ['DATABASE_URL']=f'sqlite+pysqlite:///{db}'
os.environ['REDIS_URL']='redis://127.0.0.1:1/0'
os.environ['JWT_SECRET']='x'
os.environ.pop('SEED_DEMO',None)
from conftest import login, make_study, unique_name, lock_roster

def test_viewer_read_only(client):
    admin=login(client,'admin')
    sid=make_study(client,admin,unique_name('roles'))
    viewer=login(client,'viewer');analyst=login(client,'analyst')
    assert client.post(f'/studies/{sid}/roster/lock',json={'shifts':[{'name':'A','slots':['a1','a2']}]},headers=viewer).status_code==403
    r=client.post(f'/studies/{sid}/roster/lock',json={'shifts':[{'name':'A','slots':['a1','a2']}]},headers=analyst)
    assert r.status_code==200
    eid=r.json()['entries'][0]['id']
    assert client.post(f'/studies/{sid}/roster/{eid}/claim',json={},headers=viewer).status_code==403
    assert client.get(f'/studies/{sid}/roster',headers=viewer).status_code==200
    order=r.json()['entries'][0]['assigned_order']
    assert client.post(f'/studies/{sid}/roster/{eid}/readings',json={'reading1':1,'reading2':2,'actual_order':order},headers=viewer).status_code==403
