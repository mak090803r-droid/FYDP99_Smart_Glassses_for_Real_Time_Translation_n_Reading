"""Box6 controls plus one explicit Box7 voice action. No ASR imports."""
import time
from box6_runtime import (ControlQueue, pending_actions, terminal_action,
                          recv_camera_message, capture_action)
from box6_runtime import PlaybackControls as BasePlaybackControls


class VoiceAction(str):
    def __new__(cls, command):
        obj = super().__new__(cls, "box7_voice")
        obj.command = command
        return obj


class VoiceQueue(ControlQueue):
    # Voice is separate from the existing queues so capture consumers cannot
    # silently drain a numbered command or treat a question as a capture.
    pass


class PlaybackControls(BasePlaybackControls):
    voice = None

    def poll(self, tts, key_queue, event_queue):
        # Base polling remains first: stop/disconnect/quit always wins.
        was_paused = bool(self.voice and self.voice.busy and tts.is_paused())
        result = super().poll(tts, key_queue, event_queue)
        if result:
            if self.voice is not None:
                self.voice.cancel(restore=False)
            return result
        service = self.voice
        if service is not None and service.busy and was_paused and not tts.is_paused():
            # An A/button resume during recording cancels the recording first.
            service.cancel(restore=False)
        if service is not None and service.enabled:
            command = service.take_command()
            if command is not None:
                if command.action == "pause":
                    tts.pause()
                    service.finish_command(restore=False)
                elif command.action == "resume":
                    tts.resume()
                    service.finish_command(restore=False)
                elif command.action in ("unknown", "cancel"):
                    service.finish_command(restore=True)
                else:
                    tts.stop()
                    service.finish_command(restore=False)
                    return VoiceAction(command)
        return None
