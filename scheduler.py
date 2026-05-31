import pytz
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

scheduler = BackgroundScheduler()


def _call_patient(patient_id: int):
    from database import SessionLocal
    from telephony import make_outbound_call, send_caregiver_sms

    db = SessionLocal()
    try:
        from database import Patient
        patient = db.query(Patient).filter(Patient.id == patient_id, Patient.is_active == True).first()
        if patient:
            make_outbound_call(patient)
    finally:
        db.close()


def schedule_patient(patient):
    hour, minute = patient.call_time.split(":")
    tz = pytz.timezone(patient.timezone)

    job_id = f"patient_{patient.id}"
    if scheduler.get_job(job_id):
        scheduler.remove_job(job_id)

    scheduler.add_job(
        _call_patient,
        trigger=CronTrigger(hour=int(hour), minute=int(minute), timezone=tz),
        id=job_id,
        args=[patient.id],
        replace_existing=True,
    )


def reschedule_all():
    from database import SessionLocal, Patient

    db = SessionLocal()
    try:
        patients = db.query(Patient).filter(Patient.is_active == True).all()
        for patient in patients:
            schedule_patient(patient)
    finally:
        db.close()


def start():
    if not scheduler.running:
        scheduler.start()
