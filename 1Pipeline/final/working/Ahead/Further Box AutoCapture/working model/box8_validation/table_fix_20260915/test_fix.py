import sys,json,copy,threading,time,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[2];sys.path[:0]=[str(ROOT),str(ROOT/'box8_validation')]
from box8_features import Features
from box8_tables import reconstruct,row_text
from box8_jobs import Job
from test_box8 import fixture
OUT=Path(__file__).resolve().parent

class FixTests(unittest.TestCase):
    def test_three_real_tables_exact(self):
        report=json.loads((OUT/'model_results.json').read_text())
        for case in report[:3]:
            t=case['tables'][0];self.assertTrue(t['safe'],t['warnings'])
            actual=[t['headers']]+[[c['text'] for c in row] for row in t['rows']]
            expected=([['Module','Function','Output'],['PaddleOCR','Recognizes printed text','Text'],['Table Detector','Finds table regions','Bounding box'],['Structure Model','Finds rows and columns','Table layout'],['RoBERTa','Answers text questions','Answer']] if '003_' in case['image'] else [['Condition','OCR','Table','Structure'],['Clear page','97.4%','Detected','Excellent'],['Perspective','93.1%','Detected','Good'],['Curved page','88.7%','Detected','Moderate'],['Motion blur','81.9%','Partial','Poor']])
            self.assertEqual(actual,expected)
    def test_hard_form_is_detected_but_not_guessed(self):
        case=json.loads((OUT/'model_results.json').read_text())[-1]
        self.assertTrue(case['tables']);self.assertFalse(case['tables'][0]['safe'])
    def test_real_page_automatic_reading_no_duplicate_cells(self):
        case=json.loads((OUT/'model_results.json').read_text())[2]
        doc=json.loads((OUT/case['image']).read_text());spoken=[]
        f=Features(None,lambda *a,**k:None);f.table_enabled=True;f.tables=case['tables'];f.document={}
        def read(*args):
            spoken.extend(row_text(f.tables[0],i) for i in range(len(f.tables[0]['rows'])))
            return 'finished'
        with patch.object(f,'speak',side_effect=lambda s,*a:spoken.append(s) or 'finished'),patch.object(f,'table_command',side_effect=read) as command:
            for region in doc['regions']:
                if f.automatic_table(region,None,None) is None:spoken.append(region['source_text'])
        text=' '.join(spoken)
        self.assertEqual(command.call_count,1)
        for value in ['97.4%','93.1%','88.7%','81.9%']:self.assertEqual(text.count(value),1)
        self.assertNotIn('88 7%',text)
        self.assertIn('Image quality has a direct effect',text);self.assertIn('Discussion',text)
    def test_disabled_no_worker(self):
        f=Features(SimpleNamespace(PIPELINE_DIR=ROOT),lambda *a,**k:None)
        f.document={'image':None};f.configure(False,False)
        with patch('box8_features.Job') as job:
            f.start_tables();f.idle_tables();f.automatic_table({'bbox':[0,0,1,1]},None,None)
            f.playback_window(SimpleNamespace(router=SimpleNamespace(_active=object())))
            job.assert_not_called()
    def test_disabled_cancels_and_clears(self):
        f=Features(None,lambda *a,**k:None);j=SimpleNamespace(cancel=lambda:None)
        f.table_job=j;f.tables=[{}];f.table_enabled=True
        with patch.object(j,'cancel') as cancel:
            f.configure(False,False);cancel.assert_called_once()
        self.assertEqual(f.tables,[])
    def test_late_result_after_toggle_not_published(self):
        import numpy as np
        entered=threading.Event();release=threading.Event();published=[]
        class ControlledJob:
            def __init__(self,*a):
                self.done=release;self.cancelled=threading.Event();self.error=None;self.result=[{'stale':True}];self.seconds=0
            def pause(self,*a):pass
            def start(self):entered.set();return self
            def cancel(self):pass # Deliberately simulate a late, uncancelled result.
        f=Features(SimpleNamespace(PIPELINE_DIR=ROOT),lambda *a,**k:published.append(k))
        f.document={'image':np.zeros((40,40,3),np.uint8)};f.table_enabled=True
        with patch('box8_features.Job',ControlledJob):
            f.start_tables();self.assertTrue(entered.wait(3));f.configure(False,False);f.configure(True,False);release.set();time.sleep(.1)
        self.assertEqual(f.tables,[]);self.assertFalse(any(p.get('tables') for p in published))
    def test_clipped_word_is_not_silently_ignored(self):
        objects,words=fixture();words.append(dict(text='clipped',score=.99,bbox=[20,110,80,150]))
        self.assertFalse(reconstruct([0,0,300,120],objects,words)['safe'])
    def test_missing_header_unsafe(self):
        objects,words=fixture();objects=[o for o in objects if o['label']!='table column header']
        self.assertFalse(reconstruct([0,0,300,120],objects,words)['safe'])
    def test_missing_row_label_unsafe(self):
        objects,words=fixture();words.pop(3)
        self.assertFalse(reconstruct([0,0,300,120],objects,words)['safe'])
    def test_uncertain_decimal_not_guessed(self):
        objects,words=fixture();words[4]['text']='88 7%'
        self.assertFalse(reconstruct([0,0,300,120],objects,words)['safe'])
    def test_mixed_prose_preserved_table_once(self):
        t=reconstruct([0,0,300,120],*fixture());spoken=[]
        f=Features(None,lambda *a,**k:None);f.table_enabled=True;f.tables=[t];f.document={}
        region=dict(bbox=[0,-30,300,50],effective_source_language='english',lines=[dict(text='Introduction.',bbox=[0,-30,300,-10]),dict(text='Component Voltage Current',bbox=[0,5,300,30])])
        before=copy.deepcopy(region)
        with patch.object(f,'speak',side_effect=lambda s,*a:spoken.append(s) or 'finished'),patch.object(f,'table_command',return_value='finished') as read:
            self.assertEqual(f.automatic_table(region,None,None),'finished');self.assertEqual(spoken,['Introduction.']);read.assert_called_once()
            self.assertEqual(f.automatic_table(dict(bbox=[0,50,300,80]),None,None),'finished');read.assert_called_once()
        self.assertEqual(region,before)

if __name__=='__main__':
    with (OUT/'fix_tests.log').open('w',encoding='utf-8') as log:
        result=unittest.TextTestRunner(stream=log,verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(FixTests))
    summary=dict(tests=result.testsRun,failures=len(result.failures),errors=len(result.errors),skipped=len(result.skipped))
    (OUT/'fix_tests.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
    print(summary);sys.exit(0 if result.wasSuccessful() else 1)
