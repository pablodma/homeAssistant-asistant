"""Speech-to-text transcription service using OpenAI Whisper API."""

from io import BytesIO
from typing import Optional

import structlog
from openai import AsyncOpenAI

from ..config import get_settings

logger = structlog.get_logger()

# Mapping of MIME types to file extensions supported by Whisper
MIME_TO_EXT: dict[str, str] = {
    "audio/ogg": ".ogg",
    "audio/mpeg": ".mp3",
    "audio/mp4": ".mp4",
    "audio/mp3": ".mp3",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
    "audio/webm": ".webm",
    "audio/flac": ".flac",
    "audio/x-m4a": ".m4a",
    "audio/aac": ".m4a",
}


class TranscriptionService:
    """Transcribe audio using OpenAI Whisper API."""

    def __init__(self) -> None:
        """Initialize with OpenAI client."""
        settings = get_settings()
        self._client = AsyncOpenAI(api_key=settings.openai_api_key)
        self._model = settings.whisper_model

    async def transcribe(self, audio_bytes: bytes, mime_type: str) -> Optional[str]:
        """Transcribe audio bytes to text.

        Args:
            audio_bytes: Raw audio file bytes.
            mime_type: MIME type of the audio (e.g. "audio/ogg; codecs=opus").

        Returns:
            Transcribed text, or None if transcription failed or was empty.
        """
        # Extract base MIME type (strip codec info like "; codecs=opus")
        base_mime = mime_type.split(";")[0].strip().lower()
        extension = MIME_TO_EXT.get(base_mime, ".ogg")

        logger.info(
            "Transcribing audio",
            mime_type=mime_type,
            base_mime=base_mime,
            extension=extension,
            size_bytes=len(audio_bytes),
        )

        try:
            # Wrap bytes in a file-like object with a name (Whisper needs the extension)
            audio_file = BytesIO(audio_bytes)
            audio_file.name = f"voice_message{extension}"

            transcription = await self._client.audio.transcriptions.create(
                model=self._model,
                file=audio_file,
                language="es",
            )

            text = transcription.text.strip() if transcription.text else ""

            if not text:
                logger.warning("Transcription returned empty text")
                return None

            logger.info(
                "Audio transcribed",
                text_length=len(text),
                text_preview=text[:80] + "..." if len(text) > 80 else text,
            )

            return text

        except Exception as e:
            logger.error("Transcription failed", error=str(e))
            return None


# Singleton
_service: Optional[TranscriptionService] = None


def get_transcription_service() -> TranscriptionService:
    """Get singleton TranscriptionService instance."""
    global _service
    if _service is None:
        _service = TranscriptionService()
    return _service
