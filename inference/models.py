import os
import re
from pathlib import Path
from threading import Lock

import numpy as np

SAMPLE_RATE = 24_000
MODELS_DIR = Path(os.environ.get("MODELS_DIR", "/models"))


class CpuModels:
    def __init__(self):
        import torch
        from faster_whisper import WhisperModel
        from sentence_transformers import SentenceTransformer

        torch.set_num_threads(int(os.getenv("TORCH_CPU_THREADS", "4")))
        torch.set_num_interop_threads(1)
        self.embedder = SentenceTransformer(str(MODELS_DIR / "bge-m3"), device="cpu", local_files_only=True)
        self.embedder.max_seq_length = 512
        self.whisper = WhisperModel(
            str(MODELS_DIR / "whisper"), device="cpu", compute_type="int8",
            cpu_threads=int(os.getenv("WHISPER_CPU_THREADS", "8")), local_files_only=True,
        )
        self.voice = torch.package.PackageImporter(str(MODELS_DIR / "v5_6_ru.pt")).load_pickle("tts_models", "model")
        self.voice.to(torch.device("cpu"))
        self.speaker = os.getenv("TTS_SPEAKER", "baya")
        self.embed_lock, self.voice_lock, self.whisper_lock = Lock(), Lock(), Lock()

    def embed(self, texts):
        with self.embed_lock:
            return self.embedder.encode(texts, batch_size=8, normalize_embeddings=True, show_progress_bar=False)

    def transcribe(self, body, sample_rate):
        import av
        from av.audio.resampler import AudioResampler

        pcm = np.frombuffer(body, dtype="<i2")
        audio = av.AudioFrame.from_ndarray(pcm.reshape(1, -1), format="s16", layout="mono")
        audio.sample_rate = sample_rate
        resample = AudioResampler(format="fltp", layout="mono", rate=16_000)
        frames = resample.resample(audio) + resample.resample(None)
        if not frames:
            return ""
        waveform = np.concatenate([frame.to_ndarray().ravel() for frame in frames])
        with self.whisper_lock:
            segments, _ = self.whisper.transcribe(
                waveform, language="ru", beam_size=3,
                condition_on_previous_text=False, no_speech_threshold=0.6,
                initial_prompt="Кафедра ИУ5 МГТУ имени Баумана. Расписание, преподаватели и объявления.",
            )
            return " ".join(segment.text.strip() for segment in segments).strip()

    def synthesize(self, text):
        from speech_text import normalize_speech

        with self.voice_lock:
            audio = self.voice.apply_tts(
                text=normalize_speech(text), speaker=self.speaker, sample_rate=SAMPLE_RATE,
                put_accent=True, put_yo=True,
            )
        return (np.clip(audio.detach().cpu().numpy(), -1, 1) * 32767).astype("<i2").tobytes()
