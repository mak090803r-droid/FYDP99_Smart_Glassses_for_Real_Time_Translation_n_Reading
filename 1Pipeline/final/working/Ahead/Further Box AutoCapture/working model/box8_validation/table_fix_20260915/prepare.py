import sys,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT));OUT=Path(__file__).resolve().parent
import pipeline_cli_box8 as host
import cv2
from PIL import Image
from box8_grid import grids
names=['capture_cli_001_20260915_101420','capture_cli_003_20260915_101526','capture_cli_004_20260915_101708','capture_cli_005_20260914_144056']
engine=host.load_ocr_engine('english');unwarper=host.load_unwarper()
for name in names:
    path=OUT/(name+'.png')
    if not path.exists():
        d=json.loads((ROOT/'paragraph_test_outputs'/f'{name}_box8.json').read_text(encoding='utf-8'))
        text,regions,frame,seconds=host.run_ocr(engine,d['image'],True,unwarper)
        with host.OCR_LOCK:raw=list(engine.predict(frame))
        words=host._extract_ocr_lines(raw,frame.shape)
        cv2.imwrite(str(path),frame)
        (OUT/(name+'.json')).write_text(json.dumps(dict(words=words,regions=regions)),encoding='utf-8')
    found=grids(Image.open(path).convert('RGB'))
    print('GRIDS',name,[(len(g['ys'])-1,len(g['xs'])-1,g['bbox']) for g in found],flush=True)
    for i,g in enumerate(found):Image.fromarray(g['image']).save(OUT/(name+f'_grid{i}.png'))
