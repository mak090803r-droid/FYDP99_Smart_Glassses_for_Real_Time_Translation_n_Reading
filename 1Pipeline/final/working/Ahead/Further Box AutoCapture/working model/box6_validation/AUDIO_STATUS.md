# Box6 audio implementation status — 2026-09-08

> Historical handoff. Integration and local validation were completed on 8 September 2026. See ../BOX6_REPORT.md and regression_checks.json for final results; pending-test statements below describe the earlier checkpoint, not the current state.

Implemented files:

- box6_audio.py: host router, Piper-compatible worker, bounded JSON/PCM transport, Pi receiver, and local/Pi PCM playback.
- piweb_cli3.py: copy of the actual working model/piweb_cli2.py, with audio receiver startup and cleanup, N/P/R controls, non-interactive terminal support, and a five-second stalled-video-send timeout.
- box6_validation/test_audio_transport.py: 12 offline transport/playback regression tests.

No changes were made by this audio task to pipeline_cli_box5.py, pipeline_cli_box6.py, or piweb_cli2.py. Pi2 SHA-256 remains:

38B0D236E553DED37FD09A8AAE0DE092B47352BEE66F306086AB9E63027D0FEE

## Final integration API

    from box6_audio import AudioRouter, PlaybackState
    router = AudioRouter(port=10000, event_callback=callback)
    router.start()                      # Listener runs in a daemon thread.
    router.set_pi_enabled(enabled)     # Nonblocking; stops previous sink.
    router.set_expected_peer(pi_ip)    # Optional: bind audio peer to video peer.
    tts = router.wrap_tts(piper_module)

Router properties: pi_enabled, connected, status.

Router methods: play_samples(audio, sample_rate, state=None, cancel_event=None, paused_event=None, speed_provider=None, connect_timeout=None), stop(), close().

Callbacks use callback(event_type, **payload):

- audio_route: enabled, connected, message, and route_changed=True on checkbox changes.
- audio_error: message.
- pi_control: action, one of next, previous, repeat, pause, resume, stop, toggle_pause.

The wrapper exposes speak, announce, is_speaking, wait_until_done, stop, pause, resume, is_paused, set_speed, get_speed, prefetch, check_errors, and last_error. Other attributes, including __file__, model paths, and _synthesize, remain available for existing voice/guidance loaders.

wait_until_done(timeout=None) returns true after acknowledged successful completion, false only on timeout, and raises RuntimeError for synthesis, device, transport, or playback errors. The parent segment loop should let these errors reach its recovery handler; it must not mark a failed paragraph read.

stop() cancels the current generation immediately. Inference already executing cannot be interrupted safely, but its eventual output is discarded. Queued old speech cannot revive after a new speak(). Route switching cancels the active sink and queued clips; the parent should explicitly replay the current paragraph if desired.

prefetch(text) is optional: one queued future segment; synthesis cache at most eight clips / 16 MB per voice. All inference on a voice is serialized.

## Protocol and deployment

Copy both piweb_cli3.py and box6_audio.py into the Pi's existing comms folder. Pi3 opens an outbound connection to PC TCP 10000, separately from the original camera protocol on 9999. No SSH deployment was performed.

All synthesis remains local on the PC. Audio payload is bounded little-endian signed 16-bit PCM, mono/stereo, with JSON control/acknowledgment messages. Pi completion is acknowledged after its playback backend drains, keeping the parent paragraph overlay aligned with audio completion. Heartbeats and disconnect errors prevent indefinite waits during network failure.

Pi output uses existing sounddevice when usable, otherwise existing ALSA aplay. No package installation was performed. The optional FYDP_PI_AUDIO_DEVICE environment variable selects a sounddevice/ALSA device, and FYDP_PC_IP overrides the existing default 192.168.137.1.

Playback uses a fixed 48 kHz output stream with 20 ms blocks and interpolation for the baseline speed/pitch behaviour. Pause preserves the unsent sample position. Hardware buffers may still drain roughly 20–80 ms after a pause. Stop cancels playback and clears queued speech.

## Tests completed

From the working model directory:

    & 'C:\Users\ali\Desktop\FYDP\fydp\Scripts\python.exe' 'box6_validation\test_audio_transport.py'
    & 'C:\Users\ali\Desktop\FYDP\fydp\Scripts\python.exe' -m py_compile 'box6_audio.py' 'piweb_cli3.py' 'box6_validation\test_audio_transport.py'

Result: 12 tests passed in 0.815 seconds; syntax compilation passed.

Coverage:

1. PC remains default and PCM samples are preserved.
2. Loopback Pi receives exact PCM; host completion waits for playback acknowledgment.
3. Pause/resume and speed controls propagate; stop works while paused.
4. Route changes return promptly and stop the previous sink without duplicate playback.
5. Disconnect wakes the caller and reports an error.
6. Pi speaker failure reaches the Piper caller.
7. Disconnected Pi selection never silently plays speech on the PC.
8. Stop during slow synthesis cannot resurrect canceled text.
9. Prefetch/repeat reuse a bounded synthesis cache.
10. Canonical Pi hardware-control events reach the host.
11. Invalid packet/payload lengths are rejected.
12. PCM backend output with a pause is byte-identical to uninterrupted output.

## Remaining verification

- Real Pi ALSA/sounddevice hardware, headset wake-up, and actual network jitter have not been tested. Loopback tests use a fake output backend and do not produce audible sound.
- GUI routing, segment navigation, model-backed synthesis, startup prompt suppression, and recovery depend on the parent integration in Box6 and are outside this isolated audio test.
- The GUI should suppress or defer startup speech when Audio(Pi) is selected but the Pi is not connected, while keeping its visual loading progress.
- Desktop firewall TCP 10000 and the Pi's chosen output device need to be checked during the first physical run.
- Speed changes are sampled dynamically through the passed PlaybackState (the wrapper updates it). The optional speed_provider convenience argument supplies an initial speed only.

Work stopped here as requested for the model handoff; no broader audio expansion was attempted.
