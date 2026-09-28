import sys,json,re
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT));OUT=Path(__file__).resolve().parent
import cv2,numpy as np
from PIL import Image
import pipeline_cli_box8 as host
from box8_grid import grids,transform_box
engine=host.load_ocr_engine('english')
report=json.loads((OUT/'model_results.json').read_text())
for case in report:
    if not case['tables']:continue
    grid=grids(Image.open(OUT/case['image'].replace('.json','.png')))[0]
    for w in case['tables'][0].get('evidence_words',[]):
        if not re.fullmatch(r'\d+\s+\d+\s*%',w['text']):continue
        x1,y1,x2,y2=map(int,transform_box(w['bbox'],grid['matrix']))
        crop=grid['image'][max(0,y1-4):y2+5,max(0,x1-4):x2+5]
        gray=cv2.cvtColor(crop,cv2.COLOR_RGB2GRAY)
        variants={'gray':gray,'clahe':cv2.createCLAHE(3,(4,4)).apply(gray),'otsu':cv2.threshold(gray,0,255,cv2.THRESH_BINARY|cv2.THRESH_OTSU)[1]}
        for name,variant in variants.items():
            for scale in [2,4]:
                a=cv2.resize(variant,None,fx=scale,fy=scale,interpolation=cv2.INTER_CUBIC);a=cv2.copyMakeBorder(a,20,20,20,20,cv2.BORDER_CONSTANT,value=255);a=cv2.cvtColor(a,cv2.COLOR_GRAY2BGR)
                words=host._extract_ocr_lines(list(engine.predict(a)),a.shape)
                print(case['image'],w['text'],name,scale,[v['text'] for v in words],flush=True)
