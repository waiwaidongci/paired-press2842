import json,csv,io
from sqlalchemy import select
from fastapi import HTTPException
from src.models.run import Run
from src.models.review import Review
from src.models.roster import RosterLock
from src.services import studies,roster
from src.services.domain_validation import validate_parameters
from src.services.audits import log
from src.services.queue import enqueue
from src.utils.serializers import record,now,checksum


def _snapshot(db,study_id):
    """Prefer the sealed shift roster; fall back to legacy source rows for old studies."""
    if db.scalar(select(RosterLock).where(RosterLock.study_id==study_id)):
        return roster.frozen_snapshot(db,study_id)
    rows=studies.observations(db,study_id)
    if not rows: raise HTTPException(422,'先锁定纸样名册并登记读数，或录入至少一条业务观测')
    return [{'source_key':x['source_key'],'values':x['values']} for x in rows]


def create(db,data,user):
    studies.require(db,data.study_id)
    try: params=validate_parameters(data.parameters)
    except (ValueError,TypeError) as e: raise HTTPException(422,str(e))
    snapshot=_snapshot(db,data.study_id)
    run=Run(study_id=data.study_id,name=data.name,parameters=params,snapshot=snapshot,checksum=checksum(snapshot),created_at=now(),status='queued')
    db.add(run);db.flush();log(db,user,'run.queue',str(run.id));db.commit()
    enqueue(run.id)
    return record(run)


def get(db,id):
    run=db.get(Run,id)
    if not run: raise HTTPException(404,'运行不存在')
    result=record(run);review=db.scalar(select(Review).where(Review.run_id==id));result['review']=record(review) if review else None
    return result


def list_runs(db):
    return [get(db,x) for x in db.scalars(select(Run.id).order_by(Run.id.desc()))]


def approve(db,id,data,user):
    run=db.get(Run,id)
    if not run: raise HTTPException(404,'运行不存在')
    if run.status!='completed': raise HTTPException(409,'只能复核已完成运行')
    if db.scalar(select(Review).where(Review.run_id==id)): raise HTTPException(409,'已复核，记录不可覆盖')
    obj=Review(run_id=id,note=data.note,author=user.email,created_at=now());db.add(obj);log(db,user,'review.approve',f'{id}: {data.note}');db.commit();return record(obj)


def export(db,id,user):
    r=get(db,id)
    if r['status']!='completed': raise HTTPException(409,'尚无完成结果')
    if user.role=='viewer' and not r['review']: raise HTTPException(403,'观察员只能导出已复核结果')
    stream=io.StringIO();w=csv.writer(stream);w.writerow(['运行编号','输入校验SHA256','指标','值'])
    for k,v in r['result']['metrics'].items(): w.writerow([id,r['checksum'],k,json.dumps(v,ensure_ascii=False) if isinstance(v,(dict,list)) else v])
    w.writerow([id,r['checksum'],'解释',r['result']['interpretation']])
    basis=r['result'].get('test_basis')
    if basis:
        w.writerow([id,r['checksum'],'检验依据',''])
        for k,v in basis.items(): w.writerow([id,r['checksum'],k,json.dumps(v,ensure_ascii=False) if isinstance(v,(dict,list)) else v])
    log(db,user,'run.export',id);db.commit()
    return '﻿'+stream.getvalue()
