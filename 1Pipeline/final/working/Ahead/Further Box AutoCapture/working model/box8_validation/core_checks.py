"""Real saved-page OCR/NLLB/Piper comparison. Silent audio only."""
import contextlib
import json
from pathlib import Path
import sys
import threading
import types
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
OUT=Path(__file__).resolve().parent


def main():
    import cv2
    import pipeline_cli_box7 as old
    import pipeline_cli_box8 as new
    from box6_audio import AudioRouter,RoutedTTS
    from box6_runtime import ControlQueue
    from box8_features import Features
    from box8_runtime import PlaybackControls
    from box7_validation.run_validation import SilentSink
    report={'runs':[],'physical_audio_tested':False,'microphone_tested':False}
    old._ensure_box5_tts_import_path()
    english=old.load_tts();urdu=old.load_urdu_tts(english)
    ocr=old.load_ocr_engine('chinese')
    translator,tokenizer=old.load_nllb_translator_for_output('english','urdu')
    spell=old.load_spell_corrector()
    images=[ROOT/'box6_validation/end_to_end_outputs/captures/capture_cli_001_20260908_143651.jpg',
            ROOT/'paragraph_test_outputs/live_debug_sessions/session_20260909_085902/raw_analysis_frames/analysis_00075.jpg']
    old.run_ocr(ocr,str(images[0]),False,None)
    for image,target in zip(images,['english','urdu']):
        for name,module,enabled in [('box7',old,False),('box8_off',new,False),('box8_on',new,True)]:
            events=[];sink=SilentSink();router=AudioRouter(local_player=sink)
            class Traced(RoutedTTS):
                def __init__(self,*a):super().__init__(*a);self.requests=[]
                def speak(self,text):self.requests.append(text);return super().speak(text)
            speech=Traced(english,router);document_speech=speech if target=='english' else Traced(urdu,router)
            app=types.SimpleNamespace(stop_requested=threading.Event(),voice_service=None,_voice_phase='camera',publish_frame=lambda *a:None)
            def publish(kind,**data):
                events.append((kind,data))
                if kind=='stage':app._voice_phase=data['stage']
            app.publish_event=publish
            app.box8=Features(module,publish) if module is new else None
            if app.box8:app.box8.configure(enabled,enabled)
            folder=OUT/'core_outputs'/(name+'_'+target);(folder/'captures').mkdir(parents=True,exist_ok=True)
            PlaybackControls.features=app.box8;PlaybackControls.voice=None
            try:
                with contextlib.ExitStack() as stack:
                    for key,value in dict(_GUI_CONTROLLER=app,AUDIO_ROUTER=router,PROJECT_DIR=str(folder),
                        CAPTURED_DIR=str(folder/'captures'),PARAGRAPH_TEST_DIR=str(folder),SPELL_CORRECTOR=spell,_ACTIVE_DEBUG_RECORDER=None).items():
                        stack.enter_context(patch.object(module,key,value))
                    for func in ['beep_capture','beep_failure','tone_failure','tone_success']:stack.enter_context(patch.object(module,func))
                    assert module.process_and_speak(cv2.imread(str(image)),1,'chinese',False,ocr,None,translator,tokenizer,
                        speech,ControlQueue(),ControlQueue(),output_language=target,document_tts_module=document_speech)
                data=next(d for k,d in events if k=='run_complete')
                report['runs'].append(dict(name=name,target=target,timings=data['timings'],speech_requests=document_speech.requests,
                    regions=[{k:r.get(k) for k in ['region_id','region_type','bbox','source_text','translated_text','spoken_text']} for r in data['paragraphs']],
                    pcm_clips=len(sink.records),table_started=bool(app.box8 and app.box8.table_started)))
            finally:
                if app.box8:app.box8.cancel()
                document_speech.close()
                if speech is not document_speech:speech.close()
                router.close();PlaybackControls.features=None
    for target in ['english','urdu']:
        runs=[r for r in report['runs'] if r['target']==target]
        report[target+'_regions_identical']=all(r['regions']==runs[0]['regions'] for r in runs)
        report[target+'_speech_identical']=all(r['speech_requests']==runs[0]['speech_requests'] for r in runs)
        assert report[target+'_regions_identical'] and report[target+'_speech_identical']
    (OUT/'core_checks.json').write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf-8')
    print(json.dumps([{k:r[k] for k in ['name','target','timings','pcm_clips','table_started']} for r in report['runs']],indent=2))


if __name__=='__main__':
    with (OUT/'core_checks.log').open('w',encoding='utf-8') as f,contextlib.redirect_stdout(f),contextlib.redirect_stderr(f):main()
    print('BOX8_CORE_PARITY_PASS')
