"""Cross-platform sound alerts for betting events."""

import logging
import platform
import subprocess
import threading

logger = logging.getLogger(__name__)


def _play_async(func):
    """Run sound playback in a background thread to avoid blocking."""
    t = threading.Thread(target=func, daemon=True)
    t.start()


def play_bet_alert():
    """Play a short alert sound when a betting sequence starts. Works on macOS and Windows."""
    system = platform.system()

    try:
        if system == "Darwin":
            # macOS: use built-in system sound via afplay
            def _play():
                try:
                    subprocess.run(
                        ["afplay", "/System/Library/Sounds/Glass.aiff"],
                        timeout=5,
                        capture_output=True,
                    )
                except Exception as e:
                    logger.debug("macOS sound failed: %s", e)

            _play_async(_play)

        elif system == "Windows":
            # Windows: use winsound (built-in, no dependencies)
            def _play():
                try:
                    import winsound
                    # Play a short ascending beep sequence
                    winsound.Beep(800, 150)
                    winsound.Beep(1000, 150)
                    winsound.Beep(1200, 150)
                except Exception as e:
                    logger.debug("Windows sound failed: %s", e)

            _play_async(_play)

        else:
            # Linux: try paplay (PulseAudio) or aplay (ALSA) with a beep fallback
            def _play():
                try:
                    subprocess.run(
                        ["paplay", "/usr/share/sounds/freedesktop/stereo/bell.oga"],
                        timeout=5,
                        capture_output=True,
                    )
                except FileNotFoundError:
                    try:
                        subprocess.run(
                            ["aplay", "/usr/share/sounds/alsa/Front_Center.wav"],
                            timeout=5,
                            capture_output=True,
                        )
                    except Exception as e:
                        logger.debug("Linux sound failed: %s", e)
                except Exception as e:
                    logger.debug("Linux sound failed: %s", e)

            _play_async(_play)

    except Exception as e:
        logger.debug("Sound alert failed: %s", e)
