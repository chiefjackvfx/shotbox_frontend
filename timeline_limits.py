"""Shared video-track limits for timeline import and shot creation."""

MAX_VIDEO_LAYERS = 16


def validate_video_track(track):
    if not 1 <= track <= MAX_VIDEO_LAYERS:
        raise ValueError(
            f'V{track}: only V1–V{MAX_VIDEO_LAYERS} are supported; '
            'move or remove clips on higher video tracks before importing.'
        )
