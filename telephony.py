import os
import threading
from twilio.rest import Client
from twilio.twiml.voice_response import VoiceResponse, Connect, Stream

TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_PHONE_NUMBER = os.getenv("TWILIO_PHONE_NUMBER")


def get_twilio_client():
    return Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)


def make_outbound_call(patient) -> str:
    """Place outbound call to patient. Returns call_sid."""
    client = get_twilio_client()
    ngrok_url = os.getenv("NGROK_URL", "")
    twiml_url = f"{ngrok_url}/twiml"
    print(f"[call] Calling {patient.phone} with TwiML URL: {twiml_url}")

    call = client.calls.create(
        to=patient.phone,
        from_=TWILIO_PHONE_NUMBER,
        url=twiml_url,
        method="POST",
        status_callback=f"{ngrok_url}/webhook/call",
        status_callback_method="POST",
        status_callback_event=["initiated", "answered", "completed"],
        timeout=30,
    )
    print(f"[call] Created call SID: {call.sid}")
    return call.sid


def send_caregiver_sms(caregiver_phone: str, patient_name: str, outcome: str):
    def _send():
        try:
            client = get_twilio_client()
            messages = {
                "needs_help": f"Alert: {patient_name} needs help with their medication. Please check on them.",
                "no_answer": f"Alert: Unable to reach {patient_name} for their medication reminder. Please check on them.",
            }
            body = messages.get(outcome, f"Update: {patient_name} medication call outcome: {outcome}")
            client.messages.create(
                to=caregiver_phone,
                from_=TWILIO_PHONE_NUMBER,
                body=body,
            )
            print(f"SMS sent to caregiver {caregiver_phone}")
        except Exception as e:
            print(f"SMS failed (caregiver {caregiver_phone}): {e}")

    thread = threading.Thread(target=_send, daemon=True)
    thread.start()
