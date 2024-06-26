"""core audio service.

    This handles playback of audio and speech
"""
import source.audio.speech as speech
from source.util import (check_for_signal, reset_sigint_handler,
                         start_message_bus_client, wait_for_exit_signal)
from source.util.log import LOG
from source.util.process_utils import ProcessStatus, StatusCallbackMap

# from core.audio.audioservice import AudioService


def on_ready():
    LOG.info("Audio service is ready.")


def on_error(e="Unknown"):
    LOG.error("Audio service failed to launch ({}).".format(repr(e)))


def on_stopping():
    LOG.info("Audio service is shutting down...")


def main(ready_hook=on_ready, error_hook=on_error, stopping_hook=on_stopping):
    """Start the Audio Service and connect to the Message Bus"""
    LOG.info("Starting Audio Service")
    try:
        reset_sigint_handler()
        check_for_signal("isSpeaking")
        whitelist = []
        bus = start_message_bus_client("AUDIO", whitelist=whitelist)
        # bus = start_message_bus_client("AUDIO")
        callbacks = StatusCallbackMap(
            on_ready=ready_hook, on_error=error_hook, on_stopping=stopping_hook
        )
        status = ProcessStatus("audio", bus, callbacks)

        speech.init(bus)

        # Connect audio service instance to message bus
        # audio = AudioService(bus)
        status.set_started()
    except Exception as e:
        status.set_error(e)
    else:
        # if audio.wait_for_load() and len(audio.service) > 0:
        #     # If at least one service exists, report ready
        status.set_ready()
        wait_for_exit_signal()
        status.set_stopping()
        # else:
        #     status.set_error("No audio services loaded")

        speech.shutdown()
        # audio.shutdown()


if __name__ == "__main__":
    main()
