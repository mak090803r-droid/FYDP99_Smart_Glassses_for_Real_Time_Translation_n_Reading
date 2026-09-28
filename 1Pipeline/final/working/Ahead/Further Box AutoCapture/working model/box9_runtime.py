"""Existing voice/keyboard controls plus GUI-only optional requests."""
from box7_runtime import (ControlQueue,pending_actions,terminal_action,
    recv_camera_message,capture_action,VoiceAction,PlaybackControls as VoiceControls)


class PlaybackControls(VoiceControls):
    features=None
    def poll(self,tts,key_queue,event_queue):
        result=super().poll(tts,key_queue,event_queue)
        if result:
            if self.features:
                if result in ('stop','quit','disconnect'):self.features.cancel()
                else:self.features.discard_commands()
            return result
        command=self.features.take_command() if self.features else None
        if command:
            tts.stop()
            return VoiceAction(command)
        return None
