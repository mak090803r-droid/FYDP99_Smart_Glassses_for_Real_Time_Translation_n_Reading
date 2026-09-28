"""Real cached-model smoke test; network connections and audible output disabled."""
import json
from pathlib import Path
import socket
import sys
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import numpy as np
import pipeline_cli_box6 as host


def main():
    report = {'network_connections_allowed': False, 'audio_played': False}
    previous = json.loads(Path(__file__).with_name('ocr_regression.json').read_text(encoding='utf-8'))
    image_path = previous['tests'][0]['image']
    with patch.object(socket.socket, 'connect', side_effect=RuntimeError('Network prohibited in model smoke test')):
        started = time.monotonic()
        engine = host.load_ocr_engine('chinese')
        text, regions, image, seconds = host.run_ocr(engine, image_path, False, None)
        report['ocr'] = dict(load_and_run_seconds=time.monotonic()-started, seconds=seconds,
            regions=len(regions), characters=len(text),
            baseline_text_identical=text == previous['tests'][0]['outputs']['box5']['text'])
        assert report['ocr']['baseline_text_identical'], 'Explicit local OCR weights changed recognition'
        started = time.monotonic()
        unwarper = host.load_unwarper()
        text, regions, image, seconds = host.run_ocr(engine, image_path, True, unwarper)
        report['book_mode'] = dict(load_and_run_seconds=time.monotonic()-started,
            seconds=seconds, regions=len(regions), characters=len(text), image_shape=list(image.shape))
        assert text and regions and image.size
        host._ensure_box5_tts_import_path()
        started = time.monotonic()
        english = host.load_tts()
        report['english_load_seconds'] = time.monotonic()-started
        started = time.monotonic()
        urdu = host.load_urdu_tts(english)
        report['urdu_load_seconds'] = time.monotonic()-started
        report['tts'] = []
        for language, module, phrase in (
            ('english', english, 'Paragraph one. The camera is ready. Hold still.'),
            ('urdu', urdu, '\u06cc\u06c1 \u0627\u0631\u062f\u0648 \u0622\u0648\u0627\u0632 \u06a9\u06cc \u062c\u0627\u0646\u0686 \u06c1\u06d2\u06d4')):
            started = time.monotonic()
            chunks = module._synthesize(phrase)
            seconds = time.monotonic()-started
            pcm = np.concatenate([chunk.audio_float_array for chunk in chunks])
            assert pcm.size and np.isfinite(pcm).all() and np.max(np.abs(pcm)) > 0
            report['tts'].append(dict(language=language, synthesis_seconds=seconds,
                samples=int(pcm.size), sample_rate=chunks[0].sample_rate,
                audio_duration_seconds=pcm.size/chunks[0].sample_rate))
    output = Path(__file__).with_name('local_models_smoke.json')
    output.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report, indent=2), flush=True)


if __name__ == '__main__':
    main()
