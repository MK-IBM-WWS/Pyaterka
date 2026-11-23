from __future__ import annotations

import asyncio
import json
import logging
import os
from uuid import uuid4

import httpx
from livekit import rtc
from livekit.agents import APIConnectionError, Agent, AgentServer, AgentSession, AutoSubscribe, JobContext, JobProcess, cli, llm, stt, tts
from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS
from livekit.plugins import silero

from department import GREETING, INSTRUCTIONS, UNAVAILABLE, UNKNOWN, brief, build_messages, requires_reference, search_query

log = logging.getLogger("iu5.agent")
CPU_URL = os.getenv("INFERENCE_URL", "http://inference:8650")
CPU_AUTH = {"Authorization": f"Bearer {os.getenv('INFERENCE_TOKEN', '')}"}
MODEL_ENDPOINT = os.getenv("LLM_BASE_URL", "http://llm:8080/v1")
MODEL_NAME = "iu5-qwen"


class LocalSTT(stt.STT):
    def __init__(self, client):
        super().__init__(capabilities=stt.STTCapabilities(streaming=False, interim_results=False))
        self.client = client

    async def _recognize_impl(self, buffer, *, language, conn_options):
        frame = rtc.combine_audio_frames(buffer)
        data = bytes(frame.data.cast("B"))
        if frame.num_channels != 1:
            raise APIConnectionError(message="Expected mono microphone audio")
        data = data[-90 * frame.sample_rate * 2:]
        try:
            response = await self.client.post(
                f"{CPU_URL}/stt", content=data, headers={**CPU_AUTH, "X-Sample-Rate": str(frame.sample_rate)},
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise APIConnectionError(message="Whisper is unavailable") from exc
        text = response.json()["text"]
        return stt.SpeechEvent(
            type=stt.SpeechEventType.FINAL_TRANSCRIPT,
            alternatives=[stt.SpeechData(language="ru", text=text)] if text else [],
        )


class LocalTTS(tts.TTS):
    def __init__(self, client):
        super().__init__(capabilities=tts.TTSCapabilities(streaming=False), sample_rate=24_000, num_channels=1)
        self.client = client

    def synthesize(self, text, *, conn_options=DEFAULT_API_CONNECT_OPTIONS):
        return CpuSpeech(tts=self, input_text=text, conn_options=conn_options)


class CpuSpeech(tts.ChunkedStream):
    async def _run(self, output_emitter):
        try:
            response = await self._tts.client.post(f"{CPU_URL}/tts", json={"text": self._input_text}, headers=CPU_AUTH)
            response.raise_for_status()
            if not response.content or len(response.content) % 2:
                raise ValueError("Empty or invalid PCM")
        except (httpx.HTTPError, ValueError) as exc:
            raise APIConnectionError(message="Silero TTS is unavailable") from exc
        output_emitter.initialize(request_id=uuid4().hex, sample_rate=24_000, num_channels=1,
                                  mime_type="audio/pcm", frame_size_ms=20)
        output_emitter.push(response.content)


class DepartmentLLM(llm.LLM):
    def __init__(self, client):
        super().__init__()
        self.client = client

    @property
    def model(self):
        return MODEL_NAME

    def chat(self, *, chat_ctx, tools=None, conn_options=DEFAULT_API_CONNECT_OPTIONS, **kwargs):
        return DepartmentStream(self, chat_ctx=chat_ctx, tools=[], conn_options=conn_options)


class DepartmentStream(llm.LLMStream):
    async def _run(self):
        history = [
            (message.role, message.text_content) for message in self._chat_ctx.messages()
            if message.role in {"user", "assistant"} and message.text_content and not message.interrupted
        ]
        query = search_query(history)
        reply = GREETING
        if query:
            try:
                response = await self._llm.client.post(f"{CPU_URL}/search", json={"query": query}, headers=CPU_AUTH)
                response.raise_for_status()
                sources = response.json()["results"]
                if not sources and requires_reference(query):
                    reply = UNKNOWN
                else:
                    response = await self._llm.client.post(
                        f"{MODEL_ENDPOINT}/chat/completions",
                        json={
                            "model": MODEL_NAME,
                            "messages": build_messages(history, sources),
                            "chat_template_kwargs": {"enable_thinking": False},
                            "max_tokens": 155,
                            "temperature": 0.3,
                            "top_p": 0.8,
                        },
                        timeout=90,
                    )
                    response.raise_for_status()
                    choice = response.json()["choices"][0]
                    reply = brief(choice["message"].get("content") or "", choice.get("finish_reason") == "length")
            except (httpx.HTTPError, ValueError, KeyError, IndexError):
                log.exception("Could not generate an answer")
                reply = UNAVAILABLE
        self._event_ch.send_nowait(llm.ChatChunk(
            id=uuid4().hex, delta=llm.ChoiceDelta(role="assistant", content=reply),
        ))


class DepartmentAgent(Agent):
    def __init__(self, speech, publish):
        super().__init__(instructions=INSTRUCTIONS)
        self.speech = speech
        self.publish = publish
        self.speech_id = 0

    async def on_enter(self):
        self.session.say(GREETING)

    async def tts_node(self, text, model_settings):
        final = "".join([str(part) async for part in text]).strip()
        if not final:
            return
        self.speech_id += 1
        await self.publish({"type": "agent_speech", "seq": self.speech_id, "speech_id": self.speech_id, "text": final})
        options = self.session.conn_options.tts_conn_options
        async with self.speech.synthesize(final, conn_options=options) as stream:
            async for frame in stream:
                yield frame.frame


def prewarm(proc: JobProcess):
    proc.userdata["vad"] = silero.VAD.load(min_silence_duration=0.48)


server = AgentServer(num_idle_processes=1, setup_fnc=prewarm)


@server.rtc_session()
async def entrypoint(ctx: JobContext):
    async def publish(message):
        try:
            await ctx.room.local_participant.publish_data(
                json.dumps(message, ensure_ascii=False).encode("utf-8"), reliable=True,
            )
        except Exception:
            log.exception("Failed to publish data for %s", ctx.room.name)

    tasks = set()

    def send(message):
        task = asyncio.create_task(publish(message))
        tasks.add(task)
        task.add_done_callback(tasks.discard)

    client = httpx.AsyncClient(timeout=35, trust_env=False)
    speech = LocalTTS(client)
    agent = DepartmentAgent(speech, publish)
    session = AgentSession(
        stt=LocalSTT(client), llm=DepartmentLLM(client), tts=speech, vad=ctx.proc.userdata["vad"],
        turn_handling={
            "turn_detection": "vad",
            "endpointing": {"min_delay": 0.4},
            "preemptive_generation": {"enabled": False},
        },
        aec_warmup_duration=2,
    )

    @session.on("agent_state_changed")
    def on_agent_state(event):
        if event.new_state in {"listening", "thinking", "speaking", "idle"}:
            send({"type": "agent_state", "state": event.new_state})

    @session.on("user_input_transcribed")
    def on_transcript(event):
        if event.is_final and event.transcript:
            send({"type": "user_speech", "text": event.transcript})

    async def shutdown():
        await client.aclose()
        for task in tasks.copy():
            task.cancel()

    ctx.add_shutdown_callback(shutdown)
    try:
        await ctx.connect(auto_subscribe=AutoSubscribe.AUDIO_ONLY)
        await session.start(agent=agent, room=ctx.room)
    except BaseException:
        await client.aclose()
        raise


if __name__ == "__main__":
    cli.run_app(server)
