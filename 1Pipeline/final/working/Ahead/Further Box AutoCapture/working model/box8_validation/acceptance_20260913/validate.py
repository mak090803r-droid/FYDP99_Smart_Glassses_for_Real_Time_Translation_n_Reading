"""Read-only production acceptance checks; all generated files stay here."""
import base64
import hashlib
import json
import sys
import threading
import time
import traceback
from pathlib import Path
from types import SimpleNamespace

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[1]
sys.path.insert(0, str(ROOT))
from box8_features import Features
from box8_jobs import Job
from box8_runtime import ControlQueue
from box7_commands import VoiceCommand

REPORT = {'scope': 'Real local QA/table/OCR execution; speech output recorded, not played. No live Pi, microphone, GUI or translation test.', 'pages': [], 'tables': []}
PRODUCTION = list(ROOT.glob('*.py'))

def hashes():
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in PRODUCTION}

def save():
    (OUT / 'results.json').write_text(json.dumps(REPORT, indent=2, ensure_ascii=False), encoding='utf-8')

class SilentTTS:
    def is_speaking(self): return False
    def is_paused(self): return False
    def stop(self): pass
    def pause(self): pass
    def resume(self): pass

HOST = SimpleNamespace(PIPELINE_DIR=ROOT, _GUI_CONTROLLER=None,
    _region_label=lambda r: 'paragraph ' + str(r.get('paragraph_number', r.get('region_id'))))

def feature(regions, tables=None):
    f = Features(HOST, lambda *a, **k: None)
    f.attach(dict(paragraphs=regions, output_language='english', tts=SilentTTS(),
                  box8_context={'language': 'english'}))
    f.configure(True, True)
    f.tables = tables or []
    f.spoken = []
    def speak(text, *args, **kwargs):
        f.spoken.append(text)
        return 'finished'
    f.speak = speak
    return f

def question(f, text, expected, required=None, absent=False):
    started = time.perf_counter()
    result = dict(question=text, expected=expected)
    try:
        before = len(f.spoken)
        result['action'] = f.question(text, ControlQueue(), ControlQueue())
        answer = f.last_answer or {}
        result['result'] = answer
        result['spoken'] = f.spoken[before:]
        normalized = ''.join(answer.get('answer', '').lower().split())
        result['pass'] = not normalized if absent else all(''.join(s.lower().split()) in normalized for s in (required or []))
    except Exception:
        result.update(error=traceback.format_exc(), **{'pass': False})
    result['wall_seconds'] = round(time.perf_counter() - started, 3)
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return result

def job(request):
    started = time.perf_counter()
    j = Job(ROOT, request).start()
    if not j.done.wait(120):
        j.cancel()
        raise TimeoutError('120 second table job timeout')
    if j.error: raise RuntimeError(j.error)
    return j.result, round(time.perf_counter() - started, 3), j.seconds

def main():
    before = hashes()
    REPORT['production_sha256_before'] = before
    cases = [
        ('clean', 'capture_cli_001_20260912_140524_box7.json', [
            ('Where is the small camera mounted?', 'near eye level', ['eye level'], False),
            ('What voltage does the camera require?', 'Not stated; abstain', [], True)]),
        ('truncated', 'capture_cli_005_20260912_135808_box7.json', [
            ('What causes motion blur?', 'breathing and head movement', ['breathing', 'head movement'], False),
            ('Which error rates are calculated using ground-truth text?', 'Not retained in this OCR capture; abstain', [], True)]),
        ('noisy_merged', 'capture_cli_007_20260912_132929_box7.json', [
            ('What is the first major difficulty?', 'Image acquisition', ['image acquisition'], False),
            ('What can create bright halos around letters?', 'Excessive sharpening', ['sharpening'], False)])]
    for name, filename, qs in cases:
        data = json.loads((ROOT / 'paragraph_test_outputs' / filename).read_text(encoding='utf-8'))
        f = feature(data['regions'])
        case = dict(name=name, source=filename, source_regions=data['regions'], questions=[])
        REPORT['pages'].append(case)
        for q, expected, required, absent in qs:
            case['questions'].append(question(f, q, expected, required, absent))
            save()
        f.cancel()

    from PIL import Image, ImageDraw, ImageFont
    import cv2
    import pipeline_cli_box8 as host
    started = time.perf_counter()
    engine = host.load_ocr_engine('english')
    REPORT['ocr_initialization_seconds'] = round(time.perf_counter() - started, 3)
    font = ImageFont.truetype('C:/Windows/Fonts/arial.ttf', 30)
    fixtures = [
        ('ruled', 'Prototype module specifications', True,
         [['Module', 'Voltage', 'Current'], ['Camera', '5 V', '2 A'], ['Processor', '12 V', '3 A'], ['Display', '3 V', '1 A'], ['Radio', '9 V', '4 A']],
         [('What is the Voltage of the Processor?', '12 V', ['12'], False),
          ('Which module has the highest Current?', 'Radio, 4 A', ['Radio', '4'], False),
          ('What is the purchase price of the Camera?', 'Not stated; abstain', [], True)]),
        ('borderless', 'Measured processing delays', False,
         [['Stage', 'Delay', 'Accuracy'], ['Capture', '1.25 s', '98%'], ['OCR', '2.50 s', '96%'], ['Translation', '0.75 s', '94%'], ['Speech', '3.00 s', '99%']],
         [('Which stage has the lowest Delay?', 'Translation, 0.75 s', ['Translation', '0.75'], False),
          ('What is the Delay of OCR?', '2.50 s', ['2.50'], False)]),
        ('mixed_units_blank', 'Measured component consumption', True,
         [['Component', 'Current', 'Voltage'], ['Sensor', '200 mA', '3 V'], ['Processor', '1 A', '5 V'], ['Radio', '500 mA', ''], ['Camera', '300 mA', '5 V']],
         [('Which component has the highest Current?', 'Physical answer Processor 1 A; current implementation should abstain on mixed units', [], True),
          ('What is the Voltage of Radio?', 'Blank cell; abstain', [], True)])]
    for name, title, ruled, matrix, qs in fixtures:
        case = dict(name=name, expected_matrix=matrix, questions=[])
        REPORT['tables'].append(case)
        try:
            image = Image.new('RGB', (1300, 800), 'white')
            draw = ImageDraw.Draw(image)
            draw.text((100, 55), title, fill='black', font=font)
            if ruled:
                for x in (100, 460, 820, 1180): draw.line((x, 145, x, 545), fill='black', width=2)
                for y in range(145, 546, 80): draw.line((100, y, 1180, y), fill='black', width=2)
            for ri, row in enumerate(matrix):
                for ci, text in enumerate(row): draw.text((120 + ci*360, 168 + ri*80), text, fill='black', font=font)
            path = OUT / (name + '.png')
            image.save(path)
            frame = cv2.imread(str(path))
            started = time.perf_counter()
            with host.OCR_LOCK: raw = list(engine.predict(frame))
            words = host._extract_ocr_lines(raw, frame.shape)
            case['ocr_seconds'] = round(time.perf_counter() - started, 3)
            case['ocr_fragments'] = words
            tables, wall, worker = job(dict(op='tables', image=base64.b64encode(path.read_bytes()).decode(), words=words))
            case.update(tables=tables, table_wall_seconds=wall, table_worker_seconds=worker)
            actual = ([tables[0]['headers']] + [[c['text'] for c in row] for row in tables[0]['rows']]) if tables else []
            case['actual_matrix'] = actual
            norm = lambda s: ''.join(s.lower().split())
            case['exact_cell_matches'] = sum(ri < len(actual) and ci < len(actual[ri]) and norm(value) == norm(actual[ri][ci]) for ri, row in enumerate(matrix) for ci, value in enumerate(row))
            case['expected_cell_count'] = sum(map(len, matrix))
            regions = host.group_ocr_lines_into_paragraphs(host.merge_ocr_fragments_into_lines(words), frame.shape)
            f = feature(regions, tables)
            if tables:
                f.table_command(VoiceCommand('table_read', text='read table'), ControlQueue(), ControlQueue())
                case['table_read_spoken'] = f.spoken[:]
            for q, expected, required, absent in qs:
                case['questions'].append(question(f, q, expected, required, absent))
                save()
            f.cancel()
            print('TABLE_RESULT ' + json.dumps({k:case[k] for k in ('name','actual_matrix','exact_cell_matches','expected_cell_count','ocr_seconds','table_wall_seconds')}), flush=True)
        except Exception:
            case['error'] = traceback.format_exc()
            print(case['error'], flush=True)
        save()
    REPORT['production_unchanged'] = before == hashes()
    save()
    print('VALIDATION_COMPLETE production_unchanged=' + str(REPORT['production_unchanged']), flush=True)

if __name__ == '__main__':
    try: main()
    except Exception:
        REPORT['fatal_error'] = traceback.format_exc()
        save()
        raise
