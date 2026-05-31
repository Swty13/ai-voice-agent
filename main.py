import os
import json
from datetime import datetime, date
from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI, Depends, Form, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from twilio.twiml.voice_response import VoiceResponse, Connect, Stream
from sqlalchemy.orm import Session

from database import init_db, get_db, Patient, CallLog, OutcomeEnum
from telephony import make_outbound_call, send_caregiver_sms
from scheduler import reschedule_all, schedule_patient, start as start_scheduler

app = FastAPI(title="ElderCare Medication Reminder")
templates = Jinja2Templates(directory="templates")

# Track active call sessions: call_sid -> patient_id
active_calls: dict[str, int] = {}


@app.on_event("startup")
async def startup():
    init_db()
    start_scheduler()
    reschedule_all()


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, db: Session = Depends(get_db)):
    total_patients = db.query(Patient).filter(Patient.is_active == True).count()

    today_start = datetime.combine(date.today(), datetime.min.time())
    calls_today = db.query(CallLog).filter(CallLog.called_at >= today_start).count()

    took_it_today = db.query(CallLog).filter(
        CallLog.called_at >= today_start,
        CallLog.outcome == OutcomeEnum.took_it
    ).count()
    compliance_rate = round((took_it_today / calls_today * 100) if calls_today else 0)

    recent_calls = (
        db.query(CallLog)
        .join(Patient)
        .order_by(CallLog.called_at.desc())
        .limit(10)
        .all()
    )

    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "total_patients": total_patients,
            "calls_today": calls_today,
            "compliance_rate": compliance_rate,
            "recent_calls": recent_calls,
        },
    )


@app.get("/patients", response_class=HTMLResponse)
async def patients_page(request: Request, db: Session = Depends(get_db)):
    patients = db.query(Patient).filter(Patient.is_active == True).all()

    patient_data = []
    for p in patients:
        last_log = (
            db.query(CallLog)
            .filter(CallLog.patient_id == p.id)
            .order_by(CallLog.called_at.desc())
            .first()
        )
        patient_data.append({"patient": p, "last_log": last_log})

    return templates.TemplateResponse(
        request=request,
        name="patients.html",
        context={"patient_data": patient_data},
    )


@app.post("/patients")
async def add_patient(
    request: Request,
    name: str = Form(...),
    phone: str = Form(...),
    timezone: str = Form("UTC"),
    call_time: str = Form("09:00"),
    medication_name: str = Form(...),
    dosage: str = Form(...),
    caregiver_name: str = Form(""),
    caregiver_phone: str = Form(""),
    db: Session = Depends(get_db),
):
    patient = Patient(
        name=name,
        phone=phone,
        timezone=timezone,
        call_time=call_time,
        medication_name=medication_name,
        dosage=dosage,
        caregiver_name=caregiver_name or None,
        caregiver_phone=caregiver_phone or None,
    )
    db.add(patient)
    db.commit()
    db.refresh(patient)
    schedule_patient(patient)
    return RedirectResponse(url="/patients", status_code=303)


@app.post("/call/{patient_id}")
async def trigger_call(patient_id: int, db: Session = Depends(get_db)):
    patient = db.query(Patient).filter(Patient.id == patient_id).first()
    if not patient:
        return {"error": "Patient not found"}

    log = CallLog(patient_id=patient.id, called_at=datetime.utcnow())
    db.add(log)
    db.commit()
    db.refresh(log)

    try:
        call_sid = make_outbound_call(patient)
        log.call_sid = call_sid
        active_calls[call_sid] = patient.id
        db.commit()
    except Exception as e:
        log.outcome = OutcomeEnum.no_answer
        db.commit()
        return {"error": str(e)}

    return RedirectResponse(url="/patients", status_code=303)


@app.api_route("/twiml", methods=["GET", "POST"])
async def twiml_handler(request: Request):
    if request.method == "POST":
        form = await request.form()
        call_sid = form.get("CallSid", "unknown")
    else:
        call_sid = request.query_params.get("CallSid", "unknown")
    print(f"[twiml] method={request.method} CallSid={call_sid}")

    ngrok_url = os.getenv("NGROK_URL", "")
    ws_url = ngrok_url.replace("https://", "wss://").replace("http://", "ws://")

    response = VoiceResponse()
    response.say("Hello, this is your medication reminder service. Please hold.")
    connect = Connect()
    stream = Stream(url=f"{ws_url}/ws/call/{call_sid}")
    connect.append(stream)
    response.append(connect)

    print(f"[twiml] returning XML with ws={ws_url}/ws/call/{call_sid}")
    return Response(content=str(response), media_type="application/xml")


@app.post("/webhook/call")
async def call_webhook(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    call_sid = form.get("CallSid", "")
    call_status = form.get("CallStatus", "")

    if call_status in ("no-answer", "failed", "busy"):
        log = db.query(CallLog).filter(CallLog.call_sid == call_sid).first()
        if log:
            log.outcome = OutcomeEnum.no_answer
            log.answered = False
            db.commit()

            patient = db.query(Patient).filter(Patient.id == log.patient_id).first()
            if patient and patient.caregiver_phone:
                send_caregiver_sms(patient.caregiver_phone, patient.name, "no_answer")

    return {"status": "ok"}


@app.websocket("/ws/call/{call_sid}")
async def websocket_call(websocket: WebSocket, call_sid: str, db: Session = Depends(get_db)):
    await websocket.accept()

    patient_id = active_calls.get(call_sid)
    if not patient_id:
        await websocket.close()
        return

    patient = db.query(Patient).filter(Patient.id == patient_id).first()
    if not patient:
        await websocket.close()
        return

    log = db.query(CallLog).filter(CallLog.call_sid == call_sid).first()
    if log:
        log.answered = True
        db.commit()

    # Read Twilio handshake messages to get stream_sid
    stream_sid = None
    try:
        async for raw in websocket.iter_text():
            msg = json.loads(raw)
            if msg.get("event") == "connected":
                continue
            if msg.get("event") == "start":
                stream_sid = msg.get("streamSid") or msg.get("start", {}).get("streamSid")
                print(f"[ws] stream_sid={stream_sid}")
                break
    except Exception as e:
        print(f"[ws] handshake error: {e}")

    if not stream_sid:
        print("[ws] no stream_sid, closing")
        await websocket.close()
        return

    outcome = "no_answer"
    transcript = ""

    try:
        from voice_agent import run_voice_pipeline
        outcome, transcript = await run_voice_pipeline(websocket, patient, stream_sid, call_sid)
    except WebSocketDisconnect:
        pass
    except Exception as e:
        print(f"Pipeline error: {e}")
    finally:
        if log:
            log.outcome = OutcomeEnum(outcome)
            log.transcript = transcript
            db.commit()

        if outcome in ("needs_help", "no_answer") and patient.caregiver_phone:
            send_caregiver_sms(patient.caregiver_phone, patient.name, outcome)

        active_calls.pop(call_sid, None)
