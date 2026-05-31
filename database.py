from sqlalchemy import create_engine, Column, Integer, String, Boolean, DateTime, Enum, ForeignKey, Text
from sqlalchemy.orm import declarative_base, sessionmaker, relationship
from datetime import datetime
import enum
import os

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./demo.db")

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class OutcomeEnum(str, enum.Enum):
    took_it = "took_it"
    not_yet = "not_yet"
    needs_help = "needs_help"
    no_answer = "no_answer"


class Patient(Base):
    __tablename__ = "patients"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    phone = Column(String, nullable=False)
    timezone = Column(String, default="UTC")
    call_time = Column(String, default="09:00")  # HH:MM format
    medication_name = Column(String, nullable=False)
    dosage = Column(String, nullable=False)
    caregiver_name = Column(String)
    caregiver_phone = Column(String)
    is_active = Column(Boolean, default=True)

    call_logs = relationship("CallLog", back_populates="patient")


class CallLog(Base):
    __tablename__ = "call_logs"

    id = Column(Integer, primary_key=True, index=True)
    patient_id = Column(Integer, ForeignKey("patients.id"))
    called_at = Column(DateTime, default=datetime.utcnow)
    answered = Column(Boolean, default=False)
    outcome = Column(Enum(OutcomeEnum), nullable=True)
    transcript = Column(Text, nullable=True)
    call_sid = Column(String, nullable=True)

    patient = relationship("Patient", back_populates="call_logs")


def init_db():
    Base.metadata.create_all(bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
