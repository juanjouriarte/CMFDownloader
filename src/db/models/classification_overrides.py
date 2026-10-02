from datetime import datetime
from sqlalchemy import Boolean, CheckConstraint, DateTime, Integer, BigInteger, String, Text, Index, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from src.db.engine import Base


class ClassificationOverride(Base):
    __tablename__='classification_overrides'
    __table_args__=(CheckConstraint("kind IN ('fm','fi')"),CheckConstraint('version>0'),CheckConstraint('length(trim(reason))>=8'))
    kind: Mapped[str]=mapped_column(String(2),primary_key=True)
    run_fondo: Mapped[str]=mapped_column(String(20),primary_key=True)
    active: Mapped[bool]=mapped_column(Boolean,nullable=False,server_default='true')
    categoria: Mapped[str]=mapped_column(String(30),nullable=False)
    grupo: Mapped[str]=mapped_column(String(80),nullable=False)
    tipo: Mapped[str]=mapped_column(String(80),nullable=False)
    nombre_cat: Mapped[str]=mapped_column(String(160),nullable=False)
    reason: Mapped[str]=mapped_column(Text,nullable=False)
    updated_by: Mapped[str]=mapped_column(String(120),nullable=False)
    updated_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),nullable=False,server_default=func.now())
    version: Mapped[int]=mapped_column(Integer,nullable=False)


class ClassificationAudit(Base):
    __tablename__='classification_audit'
    __table_args__=(CheckConstraint("kind IN ('fm','fi')"),CheckConstraint("action IN ('lock','unlock')"),Index('ix_classification_audit_fund','kind','run_fondo','id'))
    id: Mapped[int]=mapped_column(BigInteger,primary_key=True,autoincrement=True)
    kind: Mapped[str]=mapped_column(String(2),nullable=False)
    run_fondo: Mapped[str]=mapped_column(String(20),nullable=False)
    action: Mapped[str]=mapped_column(String(10),nullable=False)
    actor: Mapped[str]=mapped_column(String(120),nullable=False)
    reason: Mapped[str]=mapped_column(Text,nullable=False)
    before_state: Mapped[dict]=mapped_column(JSONB,nullable=False)
    after_state: Mapped[dict]=mapped_column(JSONB,nullable=False)
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),nullable=False,server_default=func.now())
