"""Real Piper -> Whisper -> Box8 commands; Q&A -> English/Urdu Piper (silent)."""
import contextlib
import json
from pathlib import Path
import sys
import threading
import types
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT));OUT=Path(__file__).resolve().parent


def main():
    import numpy as np
    import pipeline_cli_box8 as host
    from box8_voice import WhisperProcess
    from box8_commands import parse_command
    from box8_features import Features,handle
    from box8_runtime import PlaybackControls,ControlQueue
    from box6_audio import AudioRouter,RoutedTTS
    from box7_validation.run_validation import SilentSink
    host._ensure_box5_tts_import_path();english=host.load_tts();urdu=host.load_urdu_tts(english)
    recognizer=WhisperProcess(ROOT);report={'synthetic_commands':[],'spoken_answers':[],'physical_microphone_tested':False}
    try:
        for text in ['Read row two.','Read the voltage column.','Read the table.','Next row.','Read the source paragraph.','What voltage does the camera require?']:
            chunks=english._synthesize(text);samples=np.concatenate([np.asarray(c.audio_int16_array).reshape(-1) for c in chunks]);rate=chunks[0].sample_rate
            count=round(len(samples)*16000/rate);pcm=np.interp(np.arange(count)*rate/16000,np.arange(len(samples)),samples).astype('<i2').tobytes()
            result=recognizer.transcribe(pcm);expected=parse_command(text);actual=parse_command(result['text'])
            report['synthetic_commands'].append(dict(input=text,transcript=result['text'],passed=(expected.action,expected.number)==(actual.action,actual.number)))
    finally:recognizer.close()
    translator,tokenizer=host.load_nllb_translator_for_output('english','urdu')
    for target in ['english','urdu']:
        sink=SilentSink();router=AudioRouter(local_player=sink);tts=RoutedTTS(english if target=='english' else urdu,router)
        events=[];app=types.SimpleNamespace(_voice_phase='audio',stop_requested=threading.Event(),voice_service=None,publish_frame=lambda *a:None,publish_event=lambda k,**p:events.append((k,p)))
        f=Features(host,app.publish_event);app.box8=f;f.configure(False,True)
        doc=dict(tts=tts,status_tts=tts,image=np.zeros((200,400,3),np.uint8),output_language=target,index=0,voice_bookmark=0,
            box8_context=dict(language='english',translator=translator,tokenizer=tokenizer),paragraphs=[dict(region_id=1,region_type='paragraph',paragraph_number=1,
                source_text='The camera requires a 5 V power supply.',effective_source_language='english',bbox=[10,20,350,50],spoken_text='The camera requires a 5 V power supply.')])
        f.attach(doc);PlaybackControls.features=f;PlaybackControls.voice=None
        try:
            with patch.object(host,'_GUI_CONTROLLER',app),patch.object(host,'_LAST_DOCUMENT',doc):
                choice=handle(host,parse_command('What voltage does the camera require?'),ControlQueue(),ControlQueue(),0)
            report['spoken_answers'].append(dict(target=target,answer=f.last_answer,resume=choice,pcm_clips=len(sink.records)))
            assert f.last_answer['answer']=='5 V' and sink.records and choice==([0],False)
        finally:f.cancel();tts.close();router.close();PlaybackControls.features=None
    (OUT/'speech_checks.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    assert all(r['passed'] for r in report['synthetic_commands']),report['synthetic_commands']


if __name__=='__main__':
    with (OUT/'speech_checks.log').open('w',encoding='utf-8') as f,contextlib.redirect_stdout(f),contextlib.redirect_stderr(f):main()
    print('BOX8_SPEECH_CHECKS_PASS')
