from sqlalchemy import Column,Integer,String,Float,ForeignKey,UniqueConstraint
from src.config.database import Base


class RosterLock(Base):
    """A locked paper-sample roster. The seed makes the allocation auditable."""
    __tablename__='roster_locks'
    id=Column(Integer,primary_key=True)
    study_id=Column(Integer,ForeignKey('studies.id'),unique=True,nullable=False)
    seed=Column(String(64),nullable=False)
    locked_at=Column(String(40),nullable=False)
    locked_by=Column(String(120),nullable=False)


class RosterEntry(Base):
    """One paper-sample seat in the locked roster, with its readings."""
    __tablename__='roster_entries'
    __table_args__=(UniqueConstraint('study_id','slot_key'),)
    id=Column(Integer,primary_key=True)
    study_id=Column(Integer,ForeignKey('studies.id'),nullable=False)
    shift_index=Column(Integer,nullable=False)
    shift=Column(String(40),nullable=False)
    slot_key=Column(String(100),nullable=False)
    position=Column(Integer,nullable=False)
    assigned_order=Column(String(2),nullable=False)   # 'AB' old->new, 'BA' new->old
    claimed_by=Column(String(120),nullable=True)
    claimed_at=Column(String(40),nullable=True)
    reading1=Column(Float,nullable=True)
    reading2=Column(Float,nullable=True)
    actual_order=Column(String(2),nullable=True)
    measured_by=Column(String(120),nullable=True)
    measured_at=Column(String(40),nullable=True)
