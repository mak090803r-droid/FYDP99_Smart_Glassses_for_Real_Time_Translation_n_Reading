"""Geometry-only table reconstruction; never guess a split OCR fragment."""
import re


def ruled_structure(image, box):
    """Snap a model-detected table to a complete visible ruled grid.

    This never creates missing rules or invents cell contents. Borderless or
    incomplete grids continue through the learned structure model.
    """
    import cv2
    import numpy as np
    gray=cv2.cvtColor(np.asarray(image),cv2.COLOR_RGB2GRAY)
    binary=cv2.threshold(gray,0,255,cv2.THRESH_BINARY_INV|cv2.THRESH_OTSU)[1]
    width,height=box[2]-box[0],box[3]-box[1]
    horizontal=cv2.morphologyEx(binary,cv2.MORPH_OPEN,np.ones((1,max(20,int(width*.55))),np.uint8))
    vertical=cv2.morphologyEx(binary,cv2.MORPH_OPEN,np.ones((max(20,int(height*.65)),1),np.uint8))
    def lines(mask,horiz):
        contours,_=cv2.findContours(mask,cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)
        result=[]
        for contour in contours:
            x,y,w,h=cv2.boundingRect(contour)
            margin=max(40,width*.4)
            if x+w<box[0]-margin or x>box[2]+margin or y+h<box[1]-50 or y>box[3]+50:continue
            result.append((x,y,w,h))
        return result
    hs,vs=lines(horizontal,True),lines(vertical,False)
    if len(hs)<3 or len(vs)<3:return None
    xs=sorted(set(round(x+w/2) for x,y,w,h in vs));ys=sorted(set(round(y+h/2) for x,y,w,h in hs))
    if not 3<=len(xs)<=31 or not 3<=len(ys)<=101:return None
    # Every boundary must span the grid; reject ornamental or partial lines.
    if any(abs(x-xs[0])>5 or abs(x+w-xs[-1])>5 for x,y,w,h in hs):return None
    if any(abs(y-ys[0])>5 or abs(y+h-ys[-1])>5 for x,y,w,h in vs):return None
    objects=[dict(label='table row',score=1.0,bbox=[xs[0],a,xs[-1],b]) for a,b in zip(ys,ys[1:])]
    objects += [dict(label='table column',score=1.0,bbox=[a,ys[0],b,ys[-1]]) for a,b in zip(xs,xs[1:])]
    return [xs[0],ys[0],xs[-1],ys[-1]],objects


def area(b):
    return max(0, b[2]-b[0]) * max(0, b[3]-b[1])


def overlap(a, b):
    return area((max(a[0],b[0]),max(a[1],b[1]),min(a[2],b[2]),min(a[3],b[3])))


def reconstruct(box, objects, words, number=1):
    rows = sorted([o for o in objects if o['label']=='table row' and o['score']>=.6], key=lambda o:o['bbox'][1])
    cols = sorted([o for o in objects if o['label']=='table column' and o['score']>=.6], key=lambda o:o['bbox'][0])
    # Model duplicates must not create repeated rows/columns.
    def unique(items):
        out=[]
        for item in items:
            if not any(overlap(item['bbox'],x['bbox']) / max(1,min(area(item['bbox']),area(x['bbox']))) > .7 for x in out):
                out.append(item)
        return out
    rows,cols=unique(rows),unique(cols)
    warnings=[]
    if not 2<=len(rows)<=100 or not 2<=len(cols)<=30:
        return dict(number=number,bbox=box,rows=[],headers=[],safe=False,warnings=['Uncertain row/column structure'])
    headers=[o for o in objects if o['label']=='table column header' and o['score']>=.6]
    spans=[o for o in objects if o['label'] in ('table spanning cell','table projected row header') and o['score']>=.6]
    if spans:
        warnings.append('Merged or spanning cells detected; automatic reading disabled')
    matrix=[]
    for r in rows:
        cells=[]
        for c in cols:
            b=[c['bbox'][0],r['bbox'][1],c['bbox'][2],r['bbox'][3]]
            cells.append(dict(bbox=b,text='',confidence=1.0,fragments=[]))
        matrix.append(cells)
    ambiguous=0
    for word in words:
        wb=word['bbox']
        fraction=overlap(wb,box)/max(1,area(wb))
        if 0.05<fraction<.5:
            ambiguous+=1
        if fraction<.5:
            continue
        matches=sorted([(overlap(wb,c['bbox'])/max(1,area(wb)),ri,ci)
                        for ri,row in enumerate(matrix) for ci,c in enumerate(row)],reverse=True)
        score,ri,ci=matches[0]
        if score<.65 or (len(matches)>1 and matches[1][0]>.25):
            ambiguous+=1; continue
        matrix[ri][ci]['fragments'].append(word)
    for row in matrix:
        for cell in row:
            fragments=sorted(cell.pop('fragments'),key=lambda w:(w['bbox'][1],w['bbox'][0]))
            cell['text']=' '.join(w['text'] for w in fragments)
            cell['confidence']=min((w.get('score',0) for w in fragments),default=0)
    header_count=sum(any(overlap(r['bbox'],h['bbox'])/max(1,area(r['bbox']))>.5 for h in headers) for r in rows)
    if header_count>1:
        warnings.append('Multiple header rows; automatic reading disabled')
    if header_count==0:
        warnings.append('Column headers not identified; automatic reading disabled')
    if ambiguous:
        warnings.append(f'{ambiguous} OCR fragments cross cell boundaries')
    # Detect words inside the table envelope that no reconstructed cell covers.
    # The ambiguous counter above includes them; never silently omit a column.
    populated=[c for row in matrix for c in row if c['text']]
    if not populated or len(populated)<len(rows)*len(cols)*.4:
        warnings.append('Too few readable cells')
    if any(c['confidence']<.8 for c in populated):
        warnings.append('Low-confidence OCR cells')
    if any(re.fullmatch(r'\d+\s+\d+\s*%',c['text']) for c in populated):
        warnings.append('Ambiguous numeric spacing; check decimal punctuation')
    if any(any(c['text'] for c in row) and not row[0]['text'] for row in matrix[header_count:]):
        warnings.append('Missing row label; check table boundaries or recapture')
    names=[c['text'] or f'Column {i+1}' for i,c in enumerate(matrix[0])] if header_count==1 else [f'Column {i+1}' for i in range(len(cols))]
    return dict(number=number,bbox=box,rows=matrix[header_count:] if header_count else matrix,
        headers=names,header_rows=header_count,safe=not warnings,warnings=warnings)


def row_text(table, index):
    if not 0<=index<len(table['rows']):
        raise ValueError('That row is not present in this table.')
    return f"Row {index+1}. " + '; '.join(f"{h}: {c['text'] or 'not specified'}" for h,c in zip(table['headers'],table['rows'][index])) + '.'


def numeric_answer(question, tables):
    """Conservative numeric comparison: same units, complete numeric column."""
    q=question.lower()
    direction = 'max' if re.search(r'\b(highest|largest|most|maximum)\b',q) else 'min' if re.search(r'\b(lowest|smallest|least|minimum)\b',q) else None
    if not direction:
        return None
    candidates=[]
    for table in tables:
        if not table.get('safe'):
            continue
        for ci,header in enumerate(table['headers']):
            if not header or header.lower() not in q:
                continue
            values=[]
            for ri,row in enumerate(table['rows']):
                m=re.fullmatch(r'\s*([-+]?\d+(?:\.\d+)?)\s*([a-zA-Z%]*)\s*',row[ci]['text'])
                if not m: break
                values.append((float(m[1]),m[2].lower(),ri))
            if len(values)!=len(table['rows']) or not values or len({v[1] for v in values})!=1:
                continue
            target=(max if direction=='max' else min)(v[0] for v in values)
            winners=[v[2] for v in values if v[0]==target]
            evidence=' '.join(row_text(table,r) for r in winners)
            candidates.append(dict(answer=evidence,quote=evidence,table=table['number'],row=winners[0],bbox=table['bbox'],score=1.0,calculated=True))
    return candidates[0] if len(candidates)==1 else None
