"""Plain optional page tools tab; all Tk access stays on the main thread."""
import tkinter as tk
from tkinter import ttk
from tkinter.scrolledtext import ScrolledText
from box8_voice_gui import make_app_class as voice_app
from box8_features import Features
from box8_commands import parse_command
from box7_commands import VoiceCommand
from box8_runtime import PlaybackControls


def make_app_class(host):
    class Box8App(voice_app(host)):
        def __init__(self,root,test_mode=False):
            self.box8=None
            super().__init__(root,test_mode)
            self.box8=Features(host,self.publish_event)
            PlaybackControls.features=self.box8
            PlaybackControls.voice=self.voice_service
            root.title('FYDP Smart Glasses — Box8')
        def _build_interface(self):
            super()._build_interface()
            page=ttk.Frame(self.notebook);self.notebook.add(page,text='Page tools')
            row=ttk.Frame(page);row.pack(fill=tk.X,padx=8,pady=8)
            self.table_var=tk.BooleanVar(value=False);self.qa_var=tk.BooleanVar(value=False)
            ttk.Checkbutton(row,text='Table-aware reading',variable=self.table_var,command=self._features_changed).pack(side=tk.LEFT)
            ttk.Checkbutton(row,text='Source-linked questions',variable=self.qa_var,command=self._features_changed).pack(side=tk.LEFT,padx=12)
            ttk.Label(page,text='OFF: no optional inference. ON: CPU workers after core processing / when requested.\nAsk in English. Answers cite this page; uncertain answers/tables are refused.').pack(anchor='w',padx=8)
            row=ttk.Frame(page);row.pack(fill=tk.X,padx=8,pady=5)
            self.question_var=tk.StringVar()
            ttk.Entry(row,textvariable=self.question_var).pack(side=tk.LEFT,fill=tk.X,expand=True)
            ttk.Button(row,text='Ask',command=lambda:self._queue_feature(VoiceCommand('question',text=self.question_var.get()))).pack(side=tk.LEFT)
            for title,text in [('Read table','read table'),('Next row','next row'),('Read source','read source paragraph')]:
                ttk.Button(row,text=title,command=lambda t=text:self._queue_feature(parse_command(t))).pack(side=tk.LEFT,padx=2)
            self.feature_status=tk.StringVar(value='Both optional features are OFF')
            ttk.Label(page,textvariable=self.feature_status,wraplength=850).pack(anchor='w',padx=8,pady=5)
            self.feature_log=ScrolledText(page,font=('Consolas',10),state=tk.DISABLED,height=16)
            self.feature_log.pack(fill=tk.BOTH,expand=True,padx=8,pady=6)
        def _features_changed(self):
            try:self.box8.configure(self.table_var.get(),self.qa_var.get())
            except Exception as exc:
                self.table_var.set(self.box8.table_enabled);self.qa_var.set(self.box8.qa_enabled)
                self.feature_status.set(str(exc))
        def _queue_feature(self,command):
            if not command.text.strip():return
            if self._voice_phase not in ('camera','audio') or self.voice_service.busy:
                self.feature_status.set('Wait for the current operation to finish.');return
            try:self.box8.commands.put_nowait(command)
            except Exception:self.feature_status.set('A request is already queued.')
        def publish_event(self,event_type,**payload):
            if self.box8 and (event_type=='session_ended' or (event_type=='stage' and payload.get('stage') in ('capture','ocr','initializing'))):
                self.box8.cancel()
            super().publish_event(event_type,**payload)
        def _handle_event(self,event_type,payload):
            if event_type in ('box8','box8_answer'):
                if event_type=='box8_answer':
                    answer=payload['answer'];message='Question: '+payload['question']+'\nAnswer: '+(answer.get('answer') or answer.get('reason','No supported answer'))
                    message+='\nSource: '+answer.get('quote','')+'\nRegion: '+str(answer.get('region_id','table'))
                    if answer.get('translated'):message+=' (answer uses English translation; OCR/translation errors can propagate)'
                else:
                    message=payload.get('message','')
                    for table in payload.get('tables',[]):
                        message+=f"\nTable {table['number']} — {'READY' if table['safe'] else 'CHECK REQUIRED'}\n"
                        message+=' | '.join(table['headers'])+'\n'
                        message+='\n'.join(' | '.join(c['text'] or '[blank]' for c in row) for row in table['rows'])
                        message+='\n'+'; '.join(table['warnings'])
                    if payload.get('tables') and self.box8.table_enabled and self.box8.document:
                        import cv2
                        frame=self.box8.document['image'].copy()
                        for table in payload['tables']:
                            x1,y1,x2,y2=map(int,table['bbox'])
                            color=(0,180,0) if table['safe'] else (0,140,255)
                            cv2.rectangle(frame,(x1,y1),(x2,y2),color,3)
                            cv2.putText(frame,f"TABLE {table['number']} - "+('READY' if table['safe'] else 'CHECK'),(x1,max(24,y1-8)),cv2.FONT_HERSHEY_SIMPLEX,.7,color,2)
                        self.publish_frame('Table analysis',frame)
                self.feature_status.set(message.split('\n')[0][:180])
                self.feature_log.configure(state=tk.NORMAL);self.feature_log.insert(tk.END,message+'\n\n')
                if int(self.feature_log.index('end-1c').split('.')[0])>600:self.feature_log.delete('1.0','200.0')
                self.feature_log.see(tk.END);self.feature_log.configure(state=tk.DISABLED)
                host._safe_debug_call(host._ACTIVE_DEBUG_RECORDER,'record_event',event_type,**payload)
                return
            super()._handle_event(event_type,payload)
        def request_capture(self):
            if self.box8:self.box8.cancel()
            return super().request_capture()
        def request_stop_audio(self):
            if self.box8:self.box8.cancel()
            return super().request_stop_audio()
        def request_close(self):
            if self.box8:self.box8.cancel()
            PlaybackControls.features=None;PlaybackControls.voice=None
            return super().request_close()
    return Box8App
