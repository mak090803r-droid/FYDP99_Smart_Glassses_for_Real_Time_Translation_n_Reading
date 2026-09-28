"""Actual table-command text -> local Piper WAV; no Pi or speakers required."""
import sys,json,wave,threading,re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import numpy as np
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT));OUT=Path(__file__).resolve().parent
import pipeline_cli_box8 as host
from box8_features import Features
from box8_commands import parse_command
from box8_runtime import ControlQueue
from box8_voice import WhisperProcess
host._ensure_box5_tts_import_path();tts=host.load_tts()
results=[]
for case in json.loads((OUT/'model_results.json').read_text()):
    f=Features(host,lambda *a,**k:None);f.table_enabled=True;f.tables=case['tables']
    f.document=dict(tts=tts,output_language='english',image=np.zeros((1080,1920,3),np.uint8),paragraphs=[],box8_context={'language':'english'})
    samples=[];texts=[];rate=[22050]
    def speak(module,text,*args):
        texts.append(text)
        for chunk in module._synthesize(text):
            samples.append(np.asarray(chunk.audio_int16_array).reshape(-1));rate[0]=chunk.sample_rate
        samples.append(np.zeros(int(rate[0]*.25),dtype='int16'))
        return 'finished'
    with patch.object(host,'_GUI_CONTROLLER',None),patch.object(host,'_speak_segment_with_stop',side_effect=speak):
        action=f.table_command(parse_command('read table'),ControlQueue(),ControlQueue())
    pcm=np.concatenate(samples).astype('<i2');path=OUT/(case['image'].replace('.json','_spoken.wav'))
    with wave.open(str(path),'wb') as w:w.setnchannels(1);w.setsampwidth(2);w.setframerate(rate[0]);w.writeframes(pcm.tobytes())
    r=dict(image=case['image'],action=action,spoken=texts,wav=str(path),seconds=len(pcm)/rate[0],non_silent=bool(np.max(np.abs(pcm.astype('int32')))>0));results.append(r)
    print(json.dumps(r),flush=True)
    assert action=='finished' and r['non_silent']
(OUT/'speech_results.json').write_text(json.dumps(results,indent=2),encoding='utf-8')
# One round-trip checks the numeric pronunciation of the real synthesized row.
recognizer=WhisperProcess(ROOT)
try:
    text='Row 3. Condition: Curved page; OCR: 88.7%; Table: Detected; Structure: Moderate.'
    chunks=tts._synthesize(text);pcm=np.concatenate([np.asarray(c.audio_int16_array).reshape(-1) for c in chunks]);rate=chunks[0].sample_rate
    converted=np.interp(np.arange(round(len(pcm)*16000/rate))*rate/16000,np.arange(len(pcm)),pcm).astype('<i2').tobytes()
    result=recognizer.transcribe(converted)
    (OUT/'speech_roundtrip.json').write_text(json.dumps(dict(input=text,result=result),indent=2),encoding='utf-8');print('ROUNDTRIP',result,flush=True)
finally:recognizer.close()
