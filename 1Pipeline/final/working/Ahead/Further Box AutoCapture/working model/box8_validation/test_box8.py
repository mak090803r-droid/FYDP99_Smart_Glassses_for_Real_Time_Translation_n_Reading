import ast
import hashlib
import sys
import unittest
import types
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from box8_commands import parse_command
from box8_tables import reconstruct,numeric_answer,row_text
from box8_features import Features
from box8_runtime import PlaybackControls
from box7_runtime import ControlQueue


def fixture():
    objects=[]
    for y in (0,40,80):objects.append(dict(label='table row',score=.99,bbox=[0,y,300,y+40]))
    for x in (0,100,200):objects.append(dict(label='table column',score=.99,bbox=[x,0,x+100,120]))
    objects.append(dict(label='table column header',score=.99,bbox=[0,0,300,40]))
    words=[]
    for r,row in enumerate([['Component','Voltage','Current'],['Camera','5 V','2 A'],['Processor','5 V','3 A']]):
        for c,text in enumerate(row):words.append(dict(text=text,score=.99,bbox=[c*100+10,r*40+10,c*100+90,r*40+30]))
    return objects,words


class Tests(unittest.TestCase):
    def test_protected_hashes(self):
        for name,digest in {'pipeline_cli_box7.py':'E39D7ECF7EC7D1D81993C69C4F5B5A5AD0BCDC2E486AA428D997F6AC41A1AE00',
            'piweb_cli4.py':'E4E7FD5780B520D41D8403E8CB69A7FA586F2DDA1FE3E30CD69D7E68D2502D99',
            'box7_reading.py':'DC5E49EA8732625770ACA30DBBB02AF4ED8868D0CFAE2EBE199CFE71D904B22B',
            'box7_voice.py':'704F45BECDD21FDE1305B29A310C9A9287E052584513387F1758E54821A408E6',
            'box7_gui.py':'1527CFFC92505DC36413A6CC98348DA4BBB4E9C14A18CFF52CD0752673703BFA',
            'box7_commands.py':'05423C753478EE5E9D326D5E7021D55FACD81BD2803E1AC48AB777D56CBCC06F'}.items():
            self.assertEqual(hashlib.sha256((ROOT/name).read_bytes()).hexdigest().upper(),digest)
    def test_original_command_parity(self):
        from box7_commands import parse_command as old
        for text in ['repeat paragraph two','read headings','pause','continue reading','read from paragraph 2','skip paragraph 3','what voltage is required?']:
            self.assertEqual(parse_command(text),old(text))
    def test_new_commands(self):
        for text,action,number in [('read table','table_read',None),('read row two','table_row',2),
            ('read column 3','table_column',3),('read the voltage column','table_column_name',None),
            ('next row','table_next',None),('read the source paragraph','qa_source',None)]:
            c=parse_command(text);self.assertEqual((c.action,c.number),(action,number))
    def test_table_assignment(self):
        t=reconstruct([0,0,300,120],*fixture());self.assertTrue(t['safe'])
        self.assertEqual(t['headers'],['Component','Voltage','Current'])
        self.assertEqual(t['rows'][1][2]['text'],'3 A');self.assertIn('Voltage: 5 V',row_text(t,0))
    def test_spans_not_guessed(self):
        objects,words=fixture();objects.append(dict(label='table spanning cell',score=.9,bbox=[0,40,200,80]))
        self.assertFalse(reconstruct([0,0,300,120],objects,words)['safe'])
    def test_crossing_word_not_split(self):
        objects,words=fixture();words.append(dict(text='AMBIGUOUS',score=.99,bbox=[80,45,120,70]))
        self.assertFalse(reconstruct([0,0,300,120],objects,words)['safe'])
    def test_low_confidence_flagged(self):
        objects,words=fixture();words[4]['score']=.4
        self.assertFalse(reconstruct([0,0,300,120],objects,words)['safe'])
    def test_empty_structure(self):self.assertFalse(reconstruct([0,0,100,100],[],[])['safe'])
    def test_numeric_comparison(self):
        t=reconstruct([0,0,300,120],*fixture())
        answer=numeric_answer('Which component uses the most current?',[t]);self.assertIn('Processor',answer['answer'])
    def test_numeric_mixed_units_abstain(self):
        t=reconstruct([0,0,300,120],*fixture());t['rows'][0][2]['text']='200 mA'
        self.assertIsNone(numeric_answer('Which uses most current?',[t]))
    def test_disabled_never_starts_worker(self):
        f=Features(types.SimpleNamespace(PIPELINE_DIR=ROOT),lambda *a,**k:None)
        f.document={'image':None}
        with patch('box8_features.Job') as job:f.start_tables();job.assert_not_called()
    def test_cancel_discards_queued_requests(self):
        f=Features(None,lambda *a,**k:None);f.commands.put(parse_command('read table'));f.cancel();self.assertIsNone(f.take_command())
    def test_gui_request_does_not_need_voice_enabled(self):
        from box7_validation.test_box7 import FakeTTS
        f=Features(None,lambda *a,**k:None);f.commands.put(parse_command('read table'))
        PlaybackControls.features=f;PlaybackControls.voice=None
        try:self.assertEqual(PlaybackControls().poll(FakeTTS(True),ControlQueue(),ControlQueue()).command.action,'table_read')
        finally:PlaybackControls.features=None
    def test_stop_wins_over_gui_request(self):
        from box7_validation.test_box7 import FakeTTS
        f=Features(None,lambda *a,**k:None);f.commands.put(parse_command('read table'))
        PlaybackControls.features=f;PlaybackControls.voice=None
        keys=ControlQueue();keys.put('s')
        try:
            self.assertEqual(PlaybackControls().poll(FakeTTS(True),keys,ControlQueue()),'stop');self.assertIsNone(f.take_command())
        finally:PlaybackControls.features=None
    def test_core_algorithms_unchanged(self):
        def functions(name):return {n.name:ast.dump(n) for n in ast.parse((ROOT/name).read_text(encoding='utf-8-sig')).body if isinstance(n,ast.FunctionDef)}
        a,b=functions('pipeline_cli_box7.py'),functions('pipeline_cli_box8.py')
        for name in ['analyze_capture_frame','pre_capture_quality_loop','preprocess_image','group_ocr_lines_into_paragraphs',
            'merge_ocr_fragments_into_lines','run_translation_nllb_paragraphs_for_output','run_spell_correction','load_ocr_engine']:
            self.assertEqual(a[name],b[name],name)
    def test_gui_toggles_default_off(self):
        import tkinter as tk
        import pipeline_cli_box8 as host
        from box8_gui import make_app_class
        root=tk.Tk();root.withdraw();app=make_app_class(host)(root,test_mode=True)
        try:
            self.assertFalse(app.table_var.get());self.assertFalse(app.qa_var.get())
            self.assertIsNone(app.box8.table_job);self.assertIsNone(app.box8.qa_job)
            app.start_system();root.update()
        finally:
            app.box8.cancel();app.voice_service.close();PlaybackControls.features=None;PlaybackControls.voice=None
            for timer in root.tk.call('after','info'):root.after_cancel(timer)
            root.destroy()

    def feature_fixture(self):
        import numpy as np
        from box7_validation.test_box7 import FakeTTS,regions
        import pipeline_cli_box8 as host
        tts=FakeTTS();doc=dict(tts=tts,status_tts=tts,paragraphs=regions(),image=np.zeros((200,350,3)),
            output_language='english',index=1,voice_bookmark=1,box8_context=dict(language='english'))
        for r in doc['paragraphs']:r['effective_source_language']='english';r['translated_text']=r['spoken_text']
        events=[];app=types.SimpleNamespace(_voice_phase='audio',stop_requested=__import__('threading').Event(),
            publish_frame=lambda *a:None,voice_service=None)
        feature=Features(host,lambda k,**v:events.append((k,v)));app.box8=feature;feature.attach(doc)
        return host,doc,feature,app,events

    def test_answer_speaks_cites_and_resumes(self):
        import threading
        from box8_features import handle
        host,doc,f,app,events=self.feature_fixture();f.qa_enabled=True
        class ReadyJob:
            def __init__(self,*args):
                self.done=threading.Event();self.done.set();self.cancelled=threading.Event();self.error=None
                self.result=dict(answer='First body text',quote='First body text',region_id=2,bbox=[0,20,100,38])
            def start(self):return self
        spoken=[]
        with patch.object(host,'_GUI_CONTROLLER',app),patch.object(host,'_LAST_DOCUMENT',doc), \
             patch('box8_features.Job',ReadyJob),patch.object(host,'_speak_segment_with_stop',side_effect=lambda t,s,*a:spoken.append(s) or 'finished'):
            choice=handle(host,parse_command('What is the first text?'),ControlQueue(),ControlQueue(),current=1)
        self.assertEqual(choice,([1,2,3,4],False))
        self.assertIn('First body text',spoken[0]);self.assertTrue(any(k=='box8_answer' for k,_ in events))
        self.assertEqual(app._voice_phase,'audio')

    def test_source_read_uses_original_spoken_paragraph(self):
        from box8_features import handle
        host,doc,f,app,events=self.feature_fixture();f.last_answer=dict(region_id=4,quote='Second body text')
        spoken=[]
        with patch.object(host,'_GUI_CONTROLLER',app),patch.object(host,'_LAST_DOCUMENT',doc), \
             patch.object(host,'_speak_segment_with_stop',side_effect=lambda t,s,*a:spoken.append(s) or 'finished'):
            self.assertEqual(handle(host,parse_command('read the source paragraph'),ControlQueue(),ControlQueue(),1),([1,2,3,4],False))
        self.assertEqual(spoken,['Second body text'])

    def test_requested_table_row_and_column(self):
        host,doc,f,app,events=self.feature_fixture();f.table_enabled=True;f.table_started=True
        f.tables=[reconstruct([0,0,300,120],*fixture())]
        f.table_job=types.SimpleNamespace(done=__import__('threading').Event(),cancelled=__import__('threading').Event(),error=None)
        f.table_job.done.set()
        spoken=[]
        with patch.object(host,'_GUI_CONTROLLER',app),patch.object(f,'speak',side_effect=lambda s,*a:spoken.append(s) or 'finished'):
            f.table_command(parse_command('read row two'),ControlQueue(),ControlQueue())
            self.assertEqual(len(spoken),1);self.assertIn('Processor',spoken[0]);spoken.clear()
            f.table_command(parse_command('read the voltage column'),ControlQueue(),ControlQueue())
            self.assertEqual(len(spoken),2);self.assertTrue(all('Voltage: 5 V' in s for s in spoken))

    def test_uncertain_numeric_question_never_uses_span_model(self):
        host,doc,f,app,events=self.feature_fixture();f.qa_enabled=True
        with patch.object(f,'speak',return_value='finished'),patch('box8_features.Job') as job:
            f.question('Which component uses the most current?',ControlQueue(),ControlQueue());job.assert_not_called()
        self.assertFalse(f.last_answer['answer'])

    def test_stop_during_answer_does_not_resume(self):
        from box8_features import handle
        host,doc,f,app,events=self.feature_fixture()
        with patch.object(host,'_GUI_CONTROLLER',app),patch.object(host,'_LAST_DOCUMENT',doc),patch.object(f,'question',return_value='stop'):
            self.assertIsNone(handle(host,parse_command('What is here?'),ControlQueue(),ControlQueue(),1))

    def test_cancellation_of_wait_discards_result(self):
        import threading
        host,doc,f,app,events=self.feature_fixture()
        job=types.SimpleNamespace(done=threading.Event(),cancelled=threading.Event())
        job.cancel=lambda:job.cancelled.set()
        keys=ControlQueue();keys.put('s')
        with patch.object(host,'_GUI_CONTROLLER',app):self.assertFalse(f._wait(job,keys,ControlQueue()))
        self.assertTrue(job.cancelled.is_set())

    def test_automatic_table_does_not_mutate_paragraphs(self):
        import copy
        host,doc,f,app,events=self.feature_fixture();f.table_enabled=True
        f.tables=[reconstruct([0,0,300,120],*fixture())];before=copy.deepcopy(doc['paragraphs'])
        with patch.object(f,'table_command',return_value='finished') as speak:
            self.assertEqual(f.automatic_table(doc['paragraphs'][1],None,None),'finished')
            self.assertEqual(f.automatic_table(doc['paragraphs'][3],None,None),'finished')
            self.assertEqual(speak.call_count,1)
        self.assertEqual(doc['paragraphs'],before)

    def test_missing_assets_are_explicit(self):
        import tempfile
        from box8_jobs import check_assets
        with tempfile.TemporaryDirectory() as directory,self.assertRaisesRegex(RuntimeError,'model missing'):
            check_assets(directory,'qa')

    def test_table_job_runs_only_in_pcm_playback_window(self):
        import threading
        f=Features(None,lambda *a,**k:None)
        lock=threading.Lock();tts=types.SimpleNamespace(router=types.SimpleNamespace(_active=None),_synthesis_lock=lock)
        with patch.object(f,'start_tables') as start,patch.object(f,'pause_table') as pause:
            f.playback_window(tts);start.assert_not_called();pause.assert_called_with(True)
            tts.router._active=object();lock.acquire()
            f.playback_window(tts);start.assert_not_called();pause.assert_called_with(True)
            lock.release();f.playback_window(tts);start.assert_called_once();pause.assert_called_with(False)


if __name__=='__main__':unittest.main(verbosity=2)
