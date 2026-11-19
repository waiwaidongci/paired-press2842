from fastapi import APIRouter,Depends
from src.config.database import get_db
from src.middlewares.auth import current_user,writer
from src.schemas.roster import RosterLockInput,RosterClaimInput,RosterReadingsInput
from src.services import roster as service
router=APIRouter()
@router.get('/studies/{id}/roster')
def get_roster(id:int,db=Depends(get_db),user=Depends(current_user)): return service.get(db,id)
@router.post('/studies/{id}/roster/lock')
def lock_roster(id:int,data:RosterLockInput,db=Depends(get_db),user=Depends(writer)): return service.lock(db,id,data,user)
@router.post('/studies/{id}/roster/{entry_id}/claim')
def claim(id:int,entry_id:int,data:RosterClaimInput|None=None,db=Depends(get_db),user=Depends(writer)):
    return service.claim(db,id,entry_id,(data.worker if data else None),user)
@router.post('/studies/{id}/roster/{entry_id}/readings')
def readings(id:int,entry_id:int,data:RosterReadingsInput,db=Depends(get_db),user=Depends(writer)): return service.readings(db,id,entry_id,data,user)
