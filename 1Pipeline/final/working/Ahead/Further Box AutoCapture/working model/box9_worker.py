"""One offline request per GPU-capable optional worker; stdout is JSON only."""
import os
os.environ.update(HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',OMP_NUM_THREADS='2',TOKENIZERS_PARALLELISM='false')
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT/'.box8_deps'))
import json
import base64
import io
import time


def answer(request):
    from transformers import AutoTokenizer, AutoModelForQuestionAnswering
    import torch
    import numpy as np
    path=ROOT/'box9_models/qa'
    tokenizer=AutoTokenizer.from_pretrained(path,local_files_only=True)
    device=torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    model=AutoModelForQuestionAnswering.from_pretrained(path,local_files_only=True).to(device).eval()
    best=None
    for source in request['sources'][:24]:
        text=source['text']
        batch=tokenizer(request['question'][:500],text,truncation='only_second',max_length=384,
            stride=96,return_overflowing_tokens=True,return_offsets_mapping=True,padding=True,return_tensors='pt')
        offsets=batch.pop('offset_mapping'); batch.pop('overflow_to_sample_mapping')
        for key, value in batch.items():
            if hasattr(value, 'to'):
                batch[key] = value.to(device)
        with torch.inference_mode(): out=model(**batch)
        for i in range(len(out.start_logits)):
            starts=out.start_logits[i].detach().cpu().numpy(); ends=out.end_logits[i].detach().cpu().numpy()
            sequence=batch.sequence_ids(i)
            null=float(starts[0]+ends[0])
            valid=[j for j,s in enumerate(sequence) if s==1 and offsets[i,j,1]>offsets[i,j,0]]
            for a in sorted(valid,key=lambda j:starts[j],reverse=True)[:12]:
                for b in range(a,min(a+45,len(sequence))):
                    if b not in valid: continue
                    margin=float(starts[a]+ends[b])-null
                    if margin<2.0: continue
                    begin,end=int(offsets[i,a,0]),int(offsets[i,b,1])
                    value=text[begin:end].strip()
                    if not value: continue
                    if best is None or margin>best['margin']:
                        # The returned answer is literally a source span.
                        left=max(text.rfind('.',0,begin),text.rfind('\n',0,begin))+1
                        right=text.find('.',end)
                        quote=text[left:right+1 if right>=0 else len(text)].strip()
                        best=dict(answer=value,quote=quote,region_id=source.get('region_id'),
                            bbox=source.get('bbox'),table=source.get('table'),row=source.get('row'),
                            margin=margin,score=float(1/(1+np.exp(-min(40,margin)))),
                            source_text=text,translated=source.get('translated',False))
    if best is not None:
        best['model_device']=str(device)
        return best
    return dict(answer='',reason='No sufficiently supported answer in the captured page.',model_device=str(device))


def tables(request):
    from PIL import Image
    import torch
    from transformers import AutoImageProcessor, TableTransformerForObjectDetection, TableTransformerConfig
    from box8_tables import reconstruct, ruled_structure, overlap, area
    from box8_grid import grids, transform_box
    device=torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    image=Image.open(io.BytesIO(base64.b64decode(request['image']))).convert('RGB')
    original_image=Image.open(io.BytesIO(base64.b64decode(request['original_image']))).convert('RGB') if request.get('original_image') else image
    if original_image.size!=image.size:original_image=image
    from PIL import ImageStat
    if ImageStat.Stat(image.convert('L')).stddev[0]<2:
        return []
    result=[]
    visible_grids=grids(image)
    original_grids=grids(original_image) if request.get('original_image') else visible_grids
    # The geometry fallback is independent of learned full-page detection.
    # Re-OCR only these optional rectified crops, on CPU in this subprocess.
    if visible_grids:
        import cv2
        import numpy as np
        import pipeline_cli_box9 as host
        from paddleocr import PaddleOCR
        engine=PaddleOCR(text_detection_model_name='PP-OCRv6_medium_det',
            text_detection_model_dir=host._cached_paddle_model('PP-OCRv6_medium_det_onnx',('inference.onnx','inference.yml')),
            text_recognition_model_name='PP-OCRv6_medium_rec',
            text_recognition_model_dir=host._cached_paddle_model('PP-OCRv6_medium_rec_onnx',('inference.onnx','inference.yml')),
            use_doc_orientation_classify=False,use_doc_unwarping=False,use_textline_orientation=False,
            engine='onnxruntime',device='cpu',cpu_threads=2)
        cell_recognizer=None
        for grid in visible_grids:
            crop=grid['image'];scale=2.0
            source_grid=max(original_grids,key=lambda g:overlap(g['bbox'],grid['bbox'])/max(1,min(area(g['bbox']),area(grid['bbox']))),default=grid)
            source_crop=source_grid['image']
            enlarged=cv2.resize(crop,None,fx=scale,fy=scale,interpolation=cv2.INTER_CUBIC)
            raw=list(engine.predict(cv2.cvtColor(enlarged,cv2.COLOR_RGB2BGR)))
            words=host._extract_ocr_lines(raw,enlarged.shape)
            for word in words:word['bbox']=[v/scale for v in word['bbox']]
            # A detector word box can exclude a tiny decimal point. Retry only
            # suspicious values from the complete full-resolution grid cell.
            import re
            retries=[]
            for word in words:
                if not re.fullmatch(r'\d+\s+\d+\s*%',word['text']):continue
                started=time.monotonic();original=word['text']
                cx=(word['bbox'][0]+word['bbox'][2])/2;cy=(word['bbox'][1]+word['bbox'][3])/2
                ci=next((i for i,(a,b) in enumerate(zip(grid['xs'],grid['xs'][1:])) if a<=cx<=b),None)
                ri=next((i for i,(a,b) in enumerate(zip(grid['ys'],grid['ys'][1:])) if a<=cy<=b),None)
                readings=[]
                if ci is not None and ri is not None:
                    # Remove the grid rules, retain generous white context, and
                    # add padding after cropping so punctuation cannot be clipped.
                    if ci>=len(source_grid['xs'])-1 or ri>=len(source_grid['ys'])-1:
                        source_grid=grid;source_crop=crop
                    if cell_recognizer is None:
                        from paddleocr import TextRecognition
                        cell_recognizer=TextRecognition(model_name='PP-OCRv6_medium_rec',
                            model_dir=host._cached_paddle_model('PP-OCRv6_medium_rec_onnx',('inference.onnx','inference.yml')),
                            engine='onnxruntime',device='cpu',cpu_threads=2)
                    # Several small insets test whether a grid rule or tight crop
                    # is suppressing punctuation. Direct recognition avoids a
                    # second text detector and its punctuation-clipping box.
                    for inset in (2,3,5,8):
                        x1,x2=source_grid['xs'][ci]+inset,source_grid['xs'][ci+1]-inset
                        y1,y2=source_grid['ys'][ri]+inset,source_grid['ys'][ri+1]-inset
                        cell=source_crop[max(0,y1):min(source_crop.shape[0],y2),max(0,x1):min(source_crop.shape[1],x2)]
                        for padding in (0,2):
                            sample=cv2.copyMakeBorder(cell,padding,padding,padding,padding,
                                cv2.BORDER_CONSTANT,value=(255,255,255)) if padding else cell
                            checked=list(cell_recognizer.predict(sample))
                            value=str(checked[0].get('rec_text','')).strip().replace(' ','') if checked else ''
                            score=float(checked[0].get('rec_score',0)) if checked else 0
                            readings.append(dict(variant='original_full_resolution_cell',inset=inset,
                                padding=padding,value=value,confidence=score))
                valid=[r for r in readings if re.fullmatch(r'\d+\.\d+%',r['value'])
                       and re.sub(r'\D','',r['value'])==re.sub(r'\D','',original) and r['confidence']>=.80]
                counts={r['value']:sum(x['value']==r['value'] for x in valid) for r in valid}
                winner=max(counts,key=counts.get) if counts and max(counts.values())>=2 else None
                if winner:
                    word['verification']='Padded full-cell retry: at least two high-confidence variants agreed'
                    word['text']=winner
                    reason=f"accepted {counts[winner]} agreeing high-confidence full-cell retries"
                else:reason='preserved original; no sufficiently confident retry consensus'
                retry=dict(original_ocr=original,retry_ocr=readings,final_value=word['text'],reason=reason,
                           seconds=round(time.monotonic()-started,3),row=ri,column=ci)
                retries.append(retry)
                print(f"[TABLE CELL OCR] {retry['seconds']:.3f}s original OCR={original!r} -> retry OCR={[r['value'] for r in readings]!r} -> final={word['text']!r}; {reason}",flush=True)
            xs,ys=grid['xs'],grid['ys']
            objects=[dict(label='table row',score=1.,bbox=[xs[0],a,xs[-1],b]) for a,b in zip(ys,ys[1:])]
            objects += [dict(label='table column',score=1.,bbox=[a,ys[0],b,ys[-1]]) for a,b in zip(xs,xs[1:])]
            objects.append(dict(label='table column header',score=1.,bbox=[xs[0],ys[0],xs[-1],ys[1]]))
            table=reconstruct([xs[0],ys[0],xs[-1],ys[-1]],objects,words,len(result)+1)
            table['header_method']='First ruled row used as column labels'
            table['structure_method']='independent rectified ruled grid + local CPU crop OCR'
            table['model_device']=str(device)
            table['cell_ocr_retries']=retries
            # Preserve evidence coordinates in the host's original OCR image.
            for row in table['rows']:
                for cell in row:cell['bbox']=transform_box(cell['bbox'],grid['inverse'])
            table['bbox']=grid['bbox']
            table['evidence_words']=[dict(w,bbox=transform_box(w['bbox'],grid['inverse'])) for w in words]
            result.append(table)
    def infer(name,img,threshold):
        path=ROOT/('box9_models' if name == 'qa' else 'box8_models')/name
        processor=AutoImageProcessor.from_pretrained(path,local_files_only=True)
        config=TableTransformerConfig.from_pretrained(path,local_files_only=True)
        config.use_pretrained_backbone=False
        model=TableTransformerForObjectDetection.from_pretrained(path,local_files_only=True,config=config).to(device).eval()
        inputs=processor(images=img,return_tensors='pt')
        inputs={key:value.to(device) if hasattr(value,'to') else value for key,value in inputs.items()}
        with torch.inference_mode(): output=model(**inputs)
        detections=processor.post_process_object_detection(output,threshold=threshold,target_sizes=torch.tensor([[img.height,img.width]]))[0]
        return [dict(label=model.config.id2label[int(l)],score=float(s),bbox=b.tolist()) for s,l,b in zip(detections['scores'],detections['labels'],detections['boxes'])]
    detection=infer('table_detection',image,.8)
    for table in sorted(detection,key=lambda t:(t['bbox'][1],t['bbox'][0]))[:8]:
        box=table['bbox']
        if any(overlap(box,t['bbox'])/max(1,min(area(box),area(t['bbox'])))>.5 for t in result):continue
        if table['label']=='table rotated':
            result.append(dict(number=len(result)+1,bbox=box,rows=[],headers=[],safe=False,warnings=['Rotated table: recapture upright']))
            continue
        cropbox=[max(0,int(box[0])-35),max(0,int(box[1])-35),min(image.width,int(box[2])+35),min(image.height,int(box[3])+35)]
        if cropbox[2]<=cropbox[0] or cropbox[3]<=cropbox[1]:continue
        objects=infer('table_structure',image.crop(cropbox),.5)
        for obj in objects:
            b=obj['bbox'];obj['bbox']=[b[0]+cropbox[0],b[1]+cropbox[1],b[2]+cropbox[0],b[3]+cropbox[1]]
        grid=ruled_structure(image,box)
        if grid:
            headers=[o for o in objects if o['label']=='table column header']
            box,objects=grid
            objects+=headers
        rebuilt=reconstruct(box,objects,request['words'],len(result)+1)
        rebuilt['structure_method']=('GPU model + complete ruled grid' if grid else 'GPU table transformer')
        rebuilt['model_device']=str(device)
        result.append(rebuilt)
    from box8_grid import suspected_grids
    for box in suspected_grids(image):
        if any(overlap(box,t['bbox'])/max(1,min(area(box),area(t['bbox'])))>.5 for t in result):continue
        result.append(dict(number=len(result)+1,bbox=box,rows=[],headers=[],safe=False,
            warnings=['Visible ruled table, but a complete reliable grid could not be recovered. Flatten the page and keep fingers outside the table.'],
            structure_method='ruled-region detection; structure requires recapture'))
    return result


if __name__=='__main__':
    try:
        import torch
        torch.set_num_threads(2);torch.set_num_interop_threads(1)
        request=json.loads(sys.stdin.readline()); start=time.monotonic()
        result=answer(request) if request['op']=='qa' else tables(request)
        print(json.dumps(dict(result=result,seconds=time.monotonic()-start)),flush=True)
    except Exception as exc:
        print(json.dumps(dict(error=f'{type(exc).__name__}: {exc}')),flush=True)
