import json
import numpy as np
from sqlalchemy import select,func
from src.config.database import Base,engine,Session
from src.config.domain import DOMAIN
from src.models.user import User
from src.models.study import Study
from src.models.observation import Observation
from src.models.roster import RosterEntry,RosterLock
from src.models import run,review,audit
from src.utils.security import hash_password

def seed():
    Base.metadata.create_all(engine)
    rng=np.random.default_rng(42)
    i=DOMAIN['index']
    with Session() as db:
        for role in ('admin','analyst','viewer'):
            email=role+'@demo.local'
            if not db.scalar(select(User).where(User.email==email)): db.add(User(email=email,role=role,password_hash=hash_password('Research2026!')))
        for k,name in enumerate(DOMAIN['names']):
            study=db.scalar(select(Study).where(Study.name==name))
            if not study:
                study=Study(name=name,source_filename=DOMAIN['slug']+f'-202611-{k+1}.csv',description='独立合成业务观测 / 固定种子42 / 仅用于方法与流程演示');db.add(study);db.flush()
            rows=[]
            if i==0:
                for n in range(120): rows.append({'moisture':round(float(np.clip(rng.normal(5.2+k*.7,.7),0,100)),3),'extract':round(float(np.clip(rng.normal(34+k*2,2.4),0,100)),3)})
            elif i==1:
                rows=[{'group':'A','success':87+k*2,'failure':13-k*2},{'group':'B','success':96-k,'failure':4+k}]
            elif i==2:
                for n in range(24):
                    b=float(rng.uniform(3,6));rows.append({'before':round(b,3),'after':round(max(.1,b+rng.normal(-.45+k*.3,.5)),3)})
            elif i==3:
                for n in range(90):
                    x=round(float(rng.uniform(4,28)),1);rows.append({'porosity':x,'absorption':round(max(0,x*(.18+k*.08)+rng.normal(0,1.5)),2)})
            else: rows=[{'p0':.08+k*.02,'p1':.1+k*.025,'cost_per_contact':.12+k*.04,'budget':3000+k*500}]
            for n,values in enumerate(rows):
                key=f'DEMO-{k+1}-{n+1:04}'
                if not db.scalar(select(Observation.id).where(Observation.study_id==study.id,Observation.source_key==key)):
                    db.add(Observation(study_id=study.id,source_key=key,values=values))
            if i==2 and k==0 and not db.scalar(select(RosterLock).where(RosterLock.study_id==study.id)):
                # Sealed demo roster: two shifts x 8 seats, equal AB/BA per shift.
                # The last seat of each shift is left unmeasured so the demo shows
                # missing papers keeping their roster seats.
                from src.services.roster import allocate
                from src.utils.serializers import now
                stamp=now()
                db.add(RosterLock(study_id=study.id,seed='demo-roster-42',locked_at=stamp,locked_by='admin@demo.local'))
                seats=[('早班',[f'PROOF-E{n:02}' for n in range(1,9)]),('晚班',[f'PROOF-N{n:02}' for n in range(1,9)])]
                missing={'PROOF-E08','PROOF-N08'}
                for seat in allocate('demo-roster-42',seats):
                    row=RosterEntry(study_id=study.id,**seat,claimed_by='demo.worker',claimed_at=stamp,
                                    measured_by='demo.worker',measured_at=stamp)
                    if seat['slot_key'] not in missing:
                        base=float(np.clip(rng.normal(5.2,.6),.1,20))
                        delta=float(rng.normal(-.45+k*.25,.5))
                        old_value=round(base,3);new_value=round(max(.05,base+delta),3)
                        if seat['assigned_order']=='AB':
                            row.reading1,row.reading2=old_value,new_value
                        else:
                            row.reading1,row.reading2=new_value,old_value
                        row.actual_order=seat['assigned_order']
                    else:
                        row.claimed_by=None;row.claimed_at=None;row.measured_by=None;row.measured_at=None
                    db.add(row)
        db.commit()
        counts={model.__tablename__:db.scalar(select(func.count()).select_from(model)) for model in (User,Study,Observation)}
        print(json.dumps(counts));return counts
if __name__=='__main__': seed()
