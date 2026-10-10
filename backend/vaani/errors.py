"""Domain errors.

Every error carries a message that is safe to show to end users (web, Telegram, WhatsApp).
Internal details go in `detail`, which is logged but never returned to clients.
"""

from __future__ import annotations


class VaaniError(Exception):
    status_code = 500
    code = "internal_error"
    user_message = "Something went wrong on our side. Please try again in a moment."

    def __init__(self, user_message: str | None = None, *, detail: str | None = None):
        if user_message:
            self.user_message = user_message
        self.detail = detail or self.user_message
        super().__init__(self.detail)


class InvalidAudio(VaaniError):
    status_code = 400
    code = "invalid_audio"
    user_message = "I couldn't read that file as audio. Please send a voice note or an audio/video file."


class NoSpeechDetected(VaaniError):
    status_code = 422
    code = "no_speech"
    user_message = "I couldn't hear any speech in that recording. Please try again a little closer to the mic."


class UnsupportedFormat(VaaniError):
    status_code = 415
    code = "unsupported_format"
    user_message = "That file type isn't supported. Please send a voice note or an mp3, m4a, wav, ogg or mp4 file."


class FileTooLarge(VaaniError):
    status_code = 413
    code = "file_too_large"


class AudioTooLong(VaaniError):
    status_code = 413
    code = "audio_too_long"


class QuotaExceeded(VaaniError):
    status_code = 429
    code = "quota_exceeded"


class EngineUnavailable(VaaniError):
    status_code = 503
    code = "engine_unavailable"
    user_message = "That processing mode isn't available on this server right now."


class ProviderError(VaaniError):
    """An upstream AI provider failed. `retryable` errors are worth retrying or failing over."""

    status_code = 502
    code = "provider_error"
    user_message = "The AI service is busy right now. Please try again in a minute."

    def __init__(self, user_message: str | None = None, *, detail: str | None = None, retryable: bool = True):
        super().__init__(user_message, detail=detail)
        self.retryable = retryable


class NotFound(VaaniError):
    status_code = 404
    code = "not_found"
    user_message = "Not found."


class Unauthorized(VaaniError):
    status_code = 401
    code = "unauthorized"
    user_message = "Please refresh the page to start a new session."


class BadRequest(VaaniError):
    status_code = 400
    code = "bad_request"
    user_message = "That request wasn't valid."
