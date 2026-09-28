"""Optional page services. All model work is after core processing or on demand."""
import base64
import threading
import time
import queue
from box8_jobs import Job, check_assets
from box8_tables import overlap, area, row_text, numeric_answer


class Features:
    def __init__(self,host,emit):
        self.host=host;self.emit=emit;self.table_enabled=False;self.qa_enabled=False
        self.document=None;self.generation=0;self.table_job=None;self.qa_job=None
        self.tables=[];self.table_started=False;self.last_answer=None
        self.table_index=0;self.row_index=0;self.auto_done=set()
        self.commands=queue.Queue(maxsize=4)
        self.table_paused=False
        self.table_epoch=0
        self.table_source_image=None
    def pause_table(self,paused=True):
        self.table_paused=paused
        job=self.table_job
        if job and not job.done.is_set():job.pause(paused)
    def playback_window(self,tts):
        # RoutedTTS marks router._active only after PCM synthesis has finished.
        router=getattr(tts,'router',None)
        synthesis=getattr(tts,'_synthesis_lock',None)
        ready=router is not None and getattr(router,'_active',None) is not None and not (synthesis and synthesis.locked())
        self.pause_table(not ready)
        if ready:self.start_tables()
    def idle_tables(self):
        self.pause_table(False)
        self.start_tables()
    def take_command(self):
        try:return self.commands.get_nowait()
        except queue.Empty:return None
    def discard_commands(self):
        while self.take_command() is not None:pass
    def configure(self,table,qa):
        for enabled,kind in ((table,'table'),(qa,'qa')):
            if enabled:check_assets(self.host.PIPELINE_DIR,kind)
        self.table_enabled=bool(table);self.qa_enabled=bool(qa)
        if not table:
            self.table_epoch+=1
            if self.table_job:self.table_job.cancel()
            self.table_started=False;self.tables=[];self.auto_done=set()
        if not qa and self.qa_job:self.qa_job.cancel()
        self.emit('box8',message=f'Tables {"ON" if table else "OFF"}; source Q&A {"ON" if qa else "OFF"}')
    def cancel(self):
        self.generation+=1
        self.table_epoch+=1
        self.discard_commands()
        for job in (self.table_job,self.qa_job):
            if job:job.cancel()
        self.table_started=False
        self.table_job=None;self.qa_job=None
    def attach(self,document):
        self.cancel();self.document=document;self.tables=[];self.last_answer=None
        self.table_job=self.qa_job=None;self.auto_done=set();self.table_index=self.row_index=0
    def start_tables(self):
        if not self.table_enabled or not self.document or self.table_started:return
        if self.tables:return
        self.table_started=True
        document=self.document;generation=self.generation;epoch=self.table_epoch
        def current():return generation==self.generation and epoch==self.table_epoch and self.table_enabled
        self.emit('box8',message='Table analysis queued after core processing')
        def begin():
            try:
                import cv2
                ok,encoded=cv2.imencode('.png',document['image'])
                if not ok:raise RuntimeError('Cannot encode table image')
                if not current():return
                request=dict(op='tables',image=base64.b64encode(encoded).decode(),words=document.get('box8_words',[]))
                source=document.get('box8_table_source_image')
                if source is not None:
                    source_ok,source_encoded=cv2.imencode('.png',source)
                    if source_ok:request['original_image']=base64.b64encode(source_encoded).decode()
                job=Job(self.host.PIPELINE_DIR,request);self.table_job=job
                job.pause(self.table_paused)
                if not current():job.cancel();return
                job.start();job.done.wait()
                if not current() or job.cancelled.is_set():return
                if job.error:raise RuntimeError(job.error)
                self.tables=job.result
                for table in self.tables:
                    for retry in table.get('cell_ocr_retries',[]):
                        attempts=', '.join(dict.fromkeys(r.get('value','') or '[none]' for r in retry.get('retry_ocr',[])))
                        self.emit('box8',message=(f"Cell OCR {retry.get('seconds',0):.3f}s: "
                            f"{retry.get('original_ocr','')!r} -> {attempts} -> "
                            f"{retry.get('final_value','')!r}; {retry.get('reason','')}"))
                self.emit('box8',message=f'Table analysis: {len(self.tables)} table(s), {job.seconds:.2f}s',tables=self.tables)
            except Exception as exc:
                if current():
                    self.table_started=False
                    self.emit('box8',message='Table analysis failed: '+str(exc))
        threading.Thread(target=begin,daemon=True,name='box8-table-launch').start()
    def _wait(self,job,keys,events):
        from box8_runtime import PlaybackControls
        # A local no-audio control target; no model or microphone is started.
        target=self.document.get('status_tts') or self.document['tts']
        control=PlaybackControls()
        while not job.done.wait(.04):
            if self.host._GUI_CONTROLLER and self.host._GUI_CONTROLLER.stop_requested.is_set():
                job.cancel();return False
            action=control.poll(target,keys,events)
            if action:
                job.cancel();return False
        return not job.cancelled.is_set()
    def _translate(self,texts,source,target):
        context=self.document.get('box8_context',{})
        if source==target:return list(texts)
        translator=context.get('translator');tokenizer=context.get('tokenizer')
        if translator is None or tokenizer is None:
            raise RuntimeError('Translation model not available for this optional request')
        records=[dict(region_id=i,source_text=t) for i,t in enumerate(texts)]
        result,_=self.host.run_translation_nllb_paragraphs_for_output(records,source,target,translator,tokenizer)
        return [r['translated_text'] for r in result]
    def sources(self):
        document=self.document
        cached=document.get('box8_english_sources')
        if cached is not None:return cached
        sources=[]
        for region in document['paragraphs']:
            source_language=region.get('effective_source_language',document.get('box8_context',{}).get('language','english'))
            if source_language=='english':text=region['source_text'];translated=False
            elif document['output_language']=='english':text=region['translated_text'];translated=True
            else:text=self._translate([region['source_text']],source_language,'english')[0];translated=True
            # No source is silently truncated. Worker windows cover each chunk.
            sources.append(dict(text=text,region_id=region.get('region_id'),bbox=region['bbox'],translated=translated))
        document['box8_english_sources']=sources
        return sources
    def highlight(self,box,label):
        import cv2
        frame=self.document['image'].copy()
        if box:
            x1,y1,x2,y2=map(int,box);cv2.rectangle(frame,(x1,y1),(x2,y2),(0,200,255),3)
        app=self.host._GUI_CONTROLLER
        if app:app.publish_frame('Source / table evidence',frame)
        self.emit('box8',message=label)
    def speak(self,text,keys,events,box=None,label='Optional reading'):
        self.highlight(box,label)
        document=self.document
        output=document['output_language']
        tts=document['tts']
        if output!='english':text=self._translate([text],'english',output)[0]
        from box7_reading import segments
        for part in segments(text):
            action=self.host._speak_segment_with_stop(tts,part,keys,events)
            if action!='finished':return action
        return 'finished'
    def automatic_table(self,region,keys,events):
        """Only substitute fully-contained regions when structure is ready.

        Late results never rewind already spoken paragraphs. Mixed text/table
        paragraphs retain original reading and can be inspected on demand.
        """
        if not self.table_enabled:return None
        for i,table in enumerate(self.tables):
            lines=region.get('lines',[])
            inside=[overlap(line['bbox'],table['bbox'])/max(1,area(line['bbox']))>=.7 for line in lines]
            if lines and any(inside) and not all(inside):
                # Original OCR regions remain untouched. Split only this optional
                # utterance so prose joined to a header is not read as table data.
                effective=region.get('effective_source_language',self.document.get('box8_context',{}).get('language','english'))
                parts=[];pending=[]
                for line,is_inside in zip(lines,inside):
                    if is_inside:
                        if pending:parts.append(' '.join(pending));pending=[]
                        if not parts or parts[-1] is not None:parts.append(None)
                    else:pending.append(line['text'])
                if pending:parts.append(' '.join(pending))
                for part in parts:
                    if part is None:
                        if table['number'] in self.auto_done:continue
                        self.table_index=i
                        from box7_commands import VoiceCommand
                        result=self.table_command(VoiceCommand('table_read',text='read table'),keys,events)
                        if result=='finished':self.auto_done.add(table['number'])
                    else:
                        if effective!='english':part=self._translate([part],effective,'english')[0]
                        result=self.speak(part,keys,events,region['bbox'])
                    if result!='finished':return result
                return 'finished'
            if lines:
                if not all(inside):continue
            elif overlap(region['bbox'],table['bbox'])/max(1,area(region['bbox']))<.85:continue
            if table['number'] in self.auto_done:return 'finished'
            self.table_index=i
            from box7_commands import VoiceCommand
            result=self.table_command(VoiceCommand('table_read',text='read table'),keys,events)
            if result=='finished':self.auto_done.add(table['number'])
            return result
        return None
    def question(self,text,keys,events):
        if not self.qa_enabled:
            return self.speak('Enable source questions in the Box8 page tools tab first.',keys,events)
        sources=self.sources()
        answer=numeric_answer(text,self.tables)
        import re
        if answer is None and re.search(r'\b(highest|largest|most|maximum|lowest|smallest|least|minimum)\b',text.lower()):
            answer=dict(answer='',reason='No unambiguous complete numeric table column supports that comparison.')
        if answer is None:
            # Lexical ranking limits model work; every candidate retains its identity.
            terms=set(re.findall(r'\w+',text.lower()))-{'what','which','the','is','a','of','in','does','how','to','are'}
            ranked=list(sources)
            for table in self.tables:
                if table.get('safe'):
                    for ri,row in enumerate(table['rows']):
                        ranked.append(dict(text=row_text(table,ri),table=table['number'],row=ri,bbox=table['bbox']))
            ranked.sort(key=lambda s:len(terms & set(re.findall(r'\w+',s['text'].lower()))),reverse=True)
            generation=self.generation
            job=Job(self.host.PIPELINE_DIR,dict(op='qa',question=text,sources=ranked[:24]))
            self.qa_job=job;job.start()
            self.emit('box8',message='Answering from this page; normal reading is temporarily interrupted')
            if not self._wait(job,keys,events) or generation!=self.generation:return 'stop'
            if job.error:raise RuntimeError(job.error)
            answer=job.result
        self.last_answer=answer
        self.emit('box8_answer',question=text,answer=answer)
        if not answer.get('answer'):
            return self.speak('I could not find a sufficiently supported answer in this page.',keys,events)
        region=next((r for r in self.document['paragraphs'] if r.get('region_id')==answer.get('region_id')),None)
        label=self.host._region_label(region) if region else f"table {answer.get('table')}, row {answer.get('row',0)+1}"
        message=f"According to {label}: {answer['answer']}."
        return self.speak(message,keys,events,answer.get('bbox') or (region or {}).get('bbox'),label)
    def table_command(self,command,keys,events):
        if not self.table_enabled:return self.speak('Enable table reading in the Box8 page tools tab first.',keys,events)
        generation=self.generation
        self.pause_table(False)
        self.start_tables()
        while self.table_job is None and self.table_started and not self.tables:
            time.sleep(.02)
            if generation!=self.generation or not self.table_enabled:return 'stop'
            if self.host._GUI_CONTROLLER and self.host._GUI_CONTROLLER.stop_requested.is_set():return 'stop'
        job=self.table_job
        if job and not self._wait(job,keys,events):return 'stop'
        if generation!=self.generation:return 'stop'
        if job and job.error:raise RuntimeError(job.error)
        if job and not self.tables:self.tables=job.result or []
        if not self.tables:return self.speak('No readable table was detected on this page.',keys,events)
        action=command.action
        if action=='table_table':self.table_index=command.number-1
        if not 0<=self.table_index<len(self.tables):
            self.table_index=0;return self.speak('That table number is not present.',keys,events)
        table=self.tables[self.table_index]
        if not table['safe']:
            return self.speak('This table structure is uncertain. Please check the table panel or recapture. '+'. '.join(table['warnings']),keys,events,table['bbox'])
        if action=='table_skip':
            self.auto_done.add(table['number'])
            return 'finished'
        if action in ('table_read','table_table'):
            result=self.speak(f"Table {table['number']}. {len(table['rows'])} data rows and {len(table['headers'])} columns. Columns: "+', '.join(table['headers'])+'.',keys,events,table['bbox'])
            if result!='finished':return result
        indices=list(range(len(table['rows'])))
        if action in ('table_row','table_next','table_previous','table_repeat'):
            row=command.number-1 if action=='table_row' else self.row_index+({'table_next':1,'table_previous':-1}.get(action,0))
            if not 0<=row<len(table['rows']):return self.speak('That row is not present.',keys,events)
            indices=[row]
        if action in ('table_column','table_column_name'):
            ci=command.number-1 if action=='table_column' else next((i for i,h in enumerate(table['headers']) if h.lower()==command.text.lower()),-1)
            if not 0<=ci<len(table['headers']):return self.speak('That column is not present.',keys,events)
            pieces=[(i,f"Row {i+1}. {table['headers'][ci]}: {r[ci]['text'] or 'blank'}.",r[ci]['bbox']) for i,r in enumerate(table['rows'])]
        else:pieces=[(i,row_text(table,i),[min(c['bbox'][0] for c in table['rows'][i]),
            min(c['bbox'][1] for c in table['rows'][i]),max(c['bbox'][2] for c in table['rows'][i]),
            max(c['bbox'][3] for c in table['rows'][i])]) for i in indices]
        for i,text,box in pieces:
            if not self.table_enabled:return 'stop'
            self.row_index=i
            # Table text is source-language OCR; translate as a feature operation.
            source=self.document.get('box8_context',{}).get('language','english')
            effective=next((r.get('effective_source_language') for r in self.document['paragraphs'] if r.get('effective_source_language')),source)
            if effective!='english':text=self._translate([text],effective,'english')[0]
            app=self.host._GUI_CONTROLLER
            phase=getattr(app,'_voice_phase',None)
            if app:app._voice_phase='audio'
            try:result=self.speak(text,keys,events,box,f"Table {table['number']}, row {i+1}")
            finally:
                if app:app._voice_phase=phase
            if result!='finished':return result
        return 'finished'


def service(host):return getattr(host._GUI_CONTROLLER,'box8',None)


def handle(host,command,keys,events,current=None):
    feature=service(host)
    if not feature:return None
    document=host._LAST_DOCUMENT
    if not document:
        from box7_reading import _feedback
        _feedback(host,'Capture a page first.',keys,events);return None
    if feature.document is not document:feature.attach(document)
    app=host._GUI_CONTROLLER
    previous=app._voice_phase;app._voice_phase='feedback'
    try:
        if command.action=='question':result=feature.question(command.text,keys,events)
        elif command.action=='qa_source':
            answer=feature.last_answer or {}
            region=next((r for r in document['paragraphs'] if r.get('region_id')==answer.get('region_id')),None)
            if region:
                result=host._speak_segment_with_stop(document['tts'],region['spoken_text'],keys,events)
            else:result=feature.speak(answer.get('quote') or 'Ask a supported document question first.',keys,events,answer.get('bbox'))
        else:result=feature.table_command(command,keys,events)
        from box7_runtime import VoiceAction
        if isinstance(result,VoiceAction):
            from box8_reading import handle as reading_handle
            return reading_handle(host,result.command,keys,events,current=current)
        if result!='finished':return None
    except Exception as exc:
        feature.emit('box8',message='Optional feature failed: '+str(exc))
        from box7_reading import _feedback
        if _feedback(host,'The optional operation failed. Normal reading is still available.',keys,events)!='finished':return None
    finally:app._voice_phase=previous
    if current is not None:
        bookmark=document.get('voice_bookmark',current)
        count=sum(bool(r.get('spoken_text','').strip()) for r in document['paragraphs'])
        return list(range(min(bookmark,max(0,count-1)),count)),False
    return None
