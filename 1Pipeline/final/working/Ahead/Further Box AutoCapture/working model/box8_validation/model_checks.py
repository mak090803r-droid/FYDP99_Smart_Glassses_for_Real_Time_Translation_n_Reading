"""Actual offline optional models and synthetic table image + existing OCR."""
import base64
import contextlib
import io
import json
import sys
import time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from box8_jobs import Job
OUT=Path(__file__).resolve().parent


def job(request):
    j=Job(ROOT,request).start()
    if not j.done.wait(120):j.cancel();raise RuntimeError('Model test timeout')
    if j.error:raise RuntimeError(j.error)
    return dict(result=j.result,seconds=j.seconds)


def main():
    from PIL import Image,ImageDraw,ImageFont
    report={}
    context='The camera requires a 5 V power supply. Disconnect the power before cleaning. This prototype processes documents locally without a cloud service.'
    sources=[dict(text=context,region_id=2,bbox=[10,20,400,100])]
    report['qa']=[dict(question=q,**job(dict(op='qa',question=q,sources=sources))) for q in [
        'What voltage does the camera require?','What should I do before cleaning?',
        'What is the price of the camera?','Who invented the camera?']]
    image=Image.new('RGB',(1100,750),'white');draw=ImageDraw.Draw(image)
    font=ImageFont.truetype('C:/Windows/Fonts/arial.ttf',26)
    draw.text((100,60),'Table 1. Electrical specifications',fill='black',font=font)
    rows=[['Component','Voltage','Current'],['Camera','5 V','2 A'],['Processor','5 V','3 A'],['Display','3 V','1 A'],['Sensor','3 V','1 A']]
    for x in [100,400,700,1000]:draw.line((x,140,x,490),fill='black',width=2)
    for y in range(140,491,70):draw.line((100,y,1000,y),fill='black',width=2)
    words=[]
    for ri,row in enumerate(rows):
        for ci,text in enumerate(row):
            xy=(120+300*ci,160+70*ri);draw.text(xy,text,fill='black',font=font)
            words.append(dict(text=text,score=.99,bbox=list(draw.textbbox(xy,text,font=font))))
    path=OUT/'table_fixture.png';image.save(path)
    report['table_geometry']=job(dict(op='tables',image=base64.b64encode(path.read_bytes()).decode(),words=words))
    import pipeline_cli_box8 as host
    engine=host.load_ocr_engine('english')
    import cv2
    frame=cv2.imread(str(path))
    with host.OCR_LOCK:raw=list(engine.predict(frame))
    words=host._extract_ocr_lines(raw,frame.shape)
    report['table_actual_ocr']=job(dict(op='tables',image=base64.b64encode(path.read_bytes()).decode(),words=words))
    report['ocr_fragments']=words
    blank=io.BytesIO();Image.new('RGB',(800,600),'white').save(blank,format='PNG')
    report['blank']=job(dict(op='tables',image=base64.b64encode(blank.getvalue()).decode(),words=[]))
    (OUT/'model_checks.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k!='ocr_fragments'},indent=2))
    assert report['qa'][0]['result']['answer']=='5 V'
    assert 'Disconnect' in report['qa'][1]['result']['answer'] or 'power' in report['qa'][1]['result']['answer']
    assert not report['qa'][2]['result']['answer']
    assert not report['qa'][3]['result']['answer']
    assert report['blank']['result']==[]
    table=next(t for t in report['table_actual_ocr']['result'] if t['safe'])
    assert table['headers']==['Component','Voltage','Current'],table['headers']
    assert [[c['text'] for c in row] for row in table['rows']]==[
        ['Camera','5V','2A'],['Processor','5V','3A'],['Display','3V','1A'],['Sensor','3V','1A']]


if __name__=='__main__':
    with (OUT/'model_checks.log').open('w',encoding='utf-8') as f,contextlib.redirect_stdout(f),contextlib.redirect_stderr(f):main()
    print('BOX8_MODEL_CHECKS_PASS')
