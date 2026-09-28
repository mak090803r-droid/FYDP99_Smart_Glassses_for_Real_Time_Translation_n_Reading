"""Real saved-image OCR -> translation -> Piper -> silent PCM sink integration.

No camera connection, speaker playback or writes to the user's capture/log folders.
"""
import contextlib
import json
from pathlib import Path
import socket
import sys
import threading
import time
import types
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import cv2
import numpy as np
import pipeline_cli_box6 as host
from box6_audio import AudioRouter
from box6_runtime import ControlQueue


class SilentSink:
    def __init__(self): self.records = []
    def play(self, pcm, sample_rate, state):
        assert pcm.size and np.isfinite(pcm).all()
        self.records.append(dict(samples=len(pcm), sample_rate=sample_rate,
            audio_seconds=len(pcm)/sample_rate, cancelled=state.cancel.is_set()))


def main():
    destination = Path(__file__).with_name('end_to_end_outputs')
    captures = destination/'captures'
    captures.mkdir(parents=True, exist_ok=True)
    sink, events = SilentSink(), []
    router = AudioRouter(local_player=sink)
    # Do not start the listener; this run deliberately exercises PC routing only.
    host.AUDIO_ROUTER = router
    report = {'audio_played': False, 'camera_used': False, 'runs': []}
    try:
        with (destination/'console.log').open('w', encoding='utf-8') as log, \
             contextlib.redirect_stdout(log), contextlib.redirect_stderr(log), \
             patch.object(socket.socket, 'connect', side_effect=RuntimeError('Network prohibited in saved-image test')):
            image_path = Path(host.CAPTURED_DIR)/'capture_cli_001_20260903_134624.jpg'
            image = cv2.imread(str(image_path))
            assert image is not None
            ocr = host.load_ocr_engine('chinese')
            translator, tokenizer = host.load_nllb_translator_for_output('english', 'urdu')
            host._ensure_box5_tts_import_path()
            english = host.load_tts()
            urdu = host.load_urdu_tts(english)
            host.SPELL_CORRECTOR = host.load_spell_corrector()
            host.PROJECT_DIR = str(destination/'isolated_project')
            host.CAPTURED_DIR = str(captures)
            host.PARAGRAPH_TEST_DIR = str(destination)
            app = types.SimpleNamespace(stop_requested=threading.Event(),
                publish_frame=lambda *a, **kw: None,
                publish_event=lambda kind, **data: events.append((kind,data)))
            host._GUI_CONTROLLER = app
            for index, (source, target, voice) in enumerate((
                ('chinese','english',english), ('english','urdu',urdu)), start=1):
                events.clear()
                first_audio = len(sink.records)
                started = time.monotonic()
                success = host.process_and_speak(image, index, source, False, ocr,
                    None, translator, tokenizer, english, ControlQueue(), ControlQueue(),
                    output_language=target, document_tts_module=voice)
                assert success
                completed = [data for kind,data in events if kind == 'run_complete']
                assert len(completed) == 1
                regions = completed[0]['paragraphs']
                ids = [region['region_id'] for region in regions]
                assert len(ids) == len(set(ids)) == 4
                assert all(region.get('source_text') and region.get('translated_text')
                           and region.get('spoken_text') and region.get('bbox') for region in regions)
                if target == 'english':
                    assert all(region['translation_status'] == 'bypassed_same_language' for region in regions)
                else:
                    assert all(region['translation_status'] == 'translated' for region in regions)
                audio = sink.records[first_audio:]
                assert len(audio) >= len(regions)
                report['runs'].append(dict(source_selection=source, target=target,
                    elapsed_seconds=time.monotonic()-started, region_ids=ids,
                    region_types=[r['region_type'] for r in regions],
                    translation_statuses=[r['translation_status'] for r in regions],
                    timings=completed[0]['timings'], pcm_clips=len(audio),
                    generated_audio_seconds=sum(clip['audio_seconds'] for clip in audio)))
    finally:
        router.close()
        host.AUDIO_ROUTER = host._GUI_CONTROLLER = None
    report['note'] = 'Wall time includes real inference but a silent instantaneous PCM sink, not real audio playback. Physical Pi/speaker operation remains untested.'
    output = Path(__file__).with_name('end_to_end_saved.json')
    output.write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report,indent=2),flush=True)


if __name__ == '__main__': main()
