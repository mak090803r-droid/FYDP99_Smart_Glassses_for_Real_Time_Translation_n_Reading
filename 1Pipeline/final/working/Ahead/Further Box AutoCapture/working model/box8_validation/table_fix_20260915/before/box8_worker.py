"""One offline request per CPU worker; stdout is JSON only."""
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
    path=ROOT/'box8_models/qa'
    tokenizer=AutoTokenizer.from_pretrained(path,local_files_only=True)
    model=AutoModelForQuestionAnswering.from_pretrained(path,local_files_only=True).eval()
    best=None
    for source in request['sources'][:24]:
        text=source['text']
        batch=tokenizer(request['question'][:500],text,truncation='only_second',max_length=384,
            stride=96,return_overflowing_tokens=True,return_offsets_mapping=True,padding=True,return_tensors='pt')
        offsets=batch.pop('offset_mapping'); batch.pop('overflow_to_sample_mapping')
        with torch.inference_mode(): out=model(**batch)
        for i in range(len(out.start_logits)):
            starts=out.start_logits[i].numpy(); ends=out.end_logits[i].numpy()
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
    return best or dict(answer='',reason='No sufficiently supported answer in the captured page.')


def tables(request):
    from PIL import Image
    import torch
    from transformers import AutoImageProcessor, TableTransformerForObjectDetection, TableTransformerConfig
    from box8_tables import reconstruct, ruled_structure
    image=Image.open(io.BytesIO(base64.b64decode(request['image']))).convert('RGB')
    def infer(name,img,threshold):
        path=ROOT/'box8_models'/name
        processor=AutoImageProcessor.from_pretrained(path,local_files_only=True)
        config=TableTransformerConfig.from_pretrained(path,local_files_only=True)
        config.use_pretrained_backbone=False
        model=TableTransformerForObjectDetection.from_pretrained(path,local_files_only=True,config=config).eval()
        with torch.inference_mode(): output=model(**processor(images=img,return_tensors='pt'))
        detections=processor.post_process_object_detection(output,threshold=threshold,target_sizes=torch.tensor([[img.height,img.width]]))[0]
        return [dict(label=model.config.id2label[int(l)],score=float(s),bbox=b.tolist()) for s,l,b in zip(detections['scores'],detections['labels'],detections['boxes'])]
    detection=infer('table_detection',image,.8)
    result=[]
    for table in sorted(detection,key=lambda t:(t['bbox'][1],t['bbox'][0]))[:8]:
        box=table['bbox']
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
        rebuilt['structure_method']='model + complete ruled grid' if grid else 'table transformer'
        result.append(rebuilt)
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
