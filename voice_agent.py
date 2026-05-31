import re
import os

try:
    from pipecat.pipeline.pipeline import Pipeline
    from pipecat.pipeline.runner import PipelineRunner
    from pipecat.pipeline.task import PipelineParams, PipelineTask
    from pipecat.services.whisper.stt import WhisperSTTService
    from pipecat.services.ollama.llm import OLLamaLLMService
    from pipecat.services.kokoro.tts import KokoroTTSService
    from pipecat.transports.websocket.fastapi import (
        FastAPIWebsocketTransport,
        FastAPIWebsocketParams,
    )
    from pipecat.serializers.twilio import TwilioFrameSerializer
    from pipecat.frames.frames import EndFrame
    from pipecat.processors.aggregators.openai_llm_context import OpenAILLMContext
    from pipecat.audio.vad.silero import SileroVADAnalyzer
    PIPECAT_AVAILABLE = True
except ImportError as e:
    print(f"[voice_agent] import error: {e}")
    PIPECAT_AVAILABLE = False

from prompts import get_system_prompt


async def run_voice_pipeline(websocket, patient, stream_sid: str, call_sid: str = "") -> tuple:
    if not PIPECAT_AVAILABLE:
        raise RuntimeError("Pipecat imports failed — check logs above")

    ollama_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1")
    ollama_model = os.getenv("OLLAMA_MODEL", "llama3.2:3b")
    kokoro_voice = os.getenv("KOKORO_VOICE", "af_sarah")

    transport = FastAPIWebsocketTransport(
        websocket=websocket,
        params=FastAPIWebsocketParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            add_wav_header=False,
            vad_enabled=True,
            vad_analyzer=SileroVADAnalyzer(sample_rate=8000),
            serializer=TwilioFrameSerializer(
                stream_sid=stream_sid,
                call_sid=call_sid,
                account_sid=os.getenv("TWILIO_ACCOUNT_SID"),
                auth_token=os.getenv("TWILIO_AUTH_TOKEN"),
                params=TwilioFrameSerializer.InputParams(auto_hang_up=False),
            ),
        ),
    )

    stt = WhisperSTTService(model="small.en")

    llm = OLLamaLLMService(
        base_url=ollama_url,
        model=ollama_model,
    )

    tts = KokoroTTSService(voice_id=kokoro_voice)

    system_prompt = get_system_prompt(
        patient.name, patient.medication_name, patient.dosage
    )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": "[Call connected. Please greet the patient now.]"},
    ]
    context = OpenAILLMContext(messages)
    context_aggregator = llm.create_context_aggregator(context)

    pipeline = Pipeline(
        [
            transport.input(),
            stt,
            context_aggregator.user(),
            llm,
            tts,
            transport.output(),
            context_aggregator.assistant(),
        ]
    )

    task = PipelineTask(pipeline, params=PipelineParams(allow_interruptions=True))

    @transport.event_handler("on_client_disconnected")
    async def on_disconnect(t, client):
        await task.queue_frames([EndFrame()])

    # Trigger the LLM to speak the initial greeting
    await task.queue_frames([context_aggregator.user().get_context_frame()])

    runner = PipelineRunner()
    await runner.run(task)

    outcome = "no_answer"
    transcript_parts = []

    for msg in context.messages:
        if msg["role"] in ("user", "assistant"):
            transcript_parts.append(f"{msg['role'].upper()}: {msg['content']}")
            if msg["role"] == "assistant":
                match = re.search(r"OUTCOME:\s*(took_it|not_yet|needs_help)", msg["content"])
                if match:
                    outcome = match.group(1)

    return outcome, "\n".join(transcript_parts)
