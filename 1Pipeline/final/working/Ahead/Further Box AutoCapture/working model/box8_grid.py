"""Optional ruled-table geometry. Imported only inside the table worker.

Grid candidates do not depend on a learned detector accepting the whole page.
Coordinates are rectified for cell assignment then mapped back for highlighting.
"""
import cv2
import numpy as np


def transform_box(box, matrix):
    x1,y1,x2,y2=box
    p=cv2.perspectiveTransform(np.float32([[[x1,y1],[x2,y1],[x2,y2],[x1,y2]]]),matrix)[0]
    return [float(p[:,0].min()),float(p[:,1].min()),float(p[:,0].max()),float(p[:,1].max())]


def _peaks(values, threshold):
    indices=np.flatnonzero(values>=threshold)
    groups=[]
    for i in indices:
        if not groups or i-groups[-1][-1]>6:groups.append([int(i)])
        else:groups[-1].append(int(i))
    return [int(round(np.mean(g))) for g in groups]


def grids(image):
    gray=cv2.cvtColor(np.asarray(image),cv2.COLOR_RGB2GRAY)
    binary=cv2.adaptiveThreshold(gray,255,cv2.ADAPTIVE_THRESH_MEAN_C,cv2.THRESH_BINARY_INV,31,12)
    # Short kernels retain rules with the residual skew of a photographed page.
    h=cv2.morphologyEx(binary,cv2.MORPH_OPEN,np.ones((1,25),np.uint8))
    v=cv2.morphologyEx(binary,cv2.MORPH_OPEN,np.ones((25,1),np.uint8))
    mask=cv2.morphologyEx(h|v,cv2.MORPH_CLOSE,np.ones((5,5),np.uint8))
    contours,_=cv2.findContours(mask,cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)
    found=[]
    for contour in sorted(contours,key=cv2.contourArea,reverse=True):
        x,y,w,hh=cv2.boundingRect(contour)
        if w<150 or hh<70 or cv2.contourArea(contour)<8000:continue
        hull=cv2.convexHull(contour)
        quad=cv2.approxPolyDP(hull,.025*cv2.arcLength(hull,True),True)
        if len(quad)!=4:
            if len(quad)>6:continue
            quad=hull
        pts=quad.reshape(-1,2).astype('float32')
        sums=pts.sum(axis=1);diff=pts[:,1]-pts[:,0]
        ordered=np.array([pts[sums.argmin()],pts[diff.argmin()],pts[sums.argmax()],pts[diff.argmax()]],dtype='float32')
        if len(np.unique(ordered,axis=0))!=4:continue
        width=int(max(np.linalg.norm(ordered[1]-ordered[0]),np.linalg.norm(ordered[2]-ordered[3])))
        height=int(max(np.linalg.norm(ordered[3]-ordered[0]),np.linalg.norm(ordered[2]-ordered[1])))
        margin=8
        target=np.float32([[margin,margin],[width+margin,margin],[width+margin,height+margin],[margin,height+margin]])
        matrix=cv2.getPerspectiveTransform(ordered,target)
        crop=cv2.warpPerspective(np.asarray(image),matrix,(width+2*margin+1,height+2*margin+1),borderValue=(255,255,255))
        cropgray=cv2.cvtColor(crop,cv2.COLOR_RGB2GRAY)
        bw=cv2.adaptiveThreshold(cropgray,255,cv2.ADAPTIVE_THRESH_MEAN_C,cv2.THRESH_BINARY_INV,31,12)
        hm=cv2.morphologyEx(bw,cv2.MORPH_OPEN,np.ones((1,max(25,width//10)),np.uint8))
        vm=cv2.morphologyEx(bw,cv2.MORPH_OPEN,np.ones((max(20,height//10),1),np.uint8))
        # Residual bowing can spread a real rule across several scan lines.
        hd=cv2.dilate(hm,np.ones((13,1),np.uint8))
        vd=cv2.dilate(vm,np.ones((1,13),np.uint8))
        ys=_peaks(np.count_nonzero(hd,axis=1),width*.65)
        xs=_peaks(np.count_nonzero(vd,axis=0),height*.65)
        if not 3<=len(xs)<=31 or not 3<=len(ys)<=101:continue
        if xs[-1]-xs[0]<width*.85 or ys[-1]-ys[0]<height*.85:continue
        if min(np.diff(xs))<14 or min(np.diff(ys))<12:continue
        # Every proposed intersection must have actual visible rule evidence.
        if any(not np.any(bw[max(0,yy-6):yy+7,max(0,xx-6):xx+7]) for yy in ys for xx in xs):continue
        inverse=np.linalg.inv(matrix)
        found.append(dict(image=crop,matrix=matrix,inverse=inverse,xs=xs,ys=ys,
                          bbox=transform_box([xs[0],ys[0],xs[-1],ys[-1]],inverse)))
        if len(found)>=8:break
    return sorted(found,key=lambda g:(g['bbox'][1],g['bbox'][0]))


def suspected_grids(image):
    """Flag strongly ruled but incomplete regions; never manufacture cells."""
    gray=cv2.cvtColor(np.asarray(image),cv2.COLOR_RGB2GRAY)
    bw=cv2.adaptiveThreshold(gray,255,cv2.ADAPTIVE_THRESH_MEAN_C,cv2.THRESH_BINARY_INV,31,12)
    h=cv2.morphologyEx(bw,cv2.MORPH_OPEN,np.ones((1,25),np.uint8))
    v=cv2.morphologyEx(bw,cv2.MORPH_OPEN,np.ones((25,1),np.uint8))
    mask=cv2.morphologyEx(h|v,cv2.MORPH_CLOSE,np.ones((5,5),np.uint8))
    contours,_=cv2.findContours(mask,cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)
    result=[]
    for c in contours:
        x,y,w,hh=cv2.boundingRect(c)
        if w<150 or hh<70 or cv2.contourArea(c)<max(8000,w*hh*.3):continue
        hc,_=cv2.findContours(h[y:y+hh,x:x+w],cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)
        vc,_=cv2.findContours(v[y:y+hh,x:x+w],cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)
        if sum(cv2.boundingRect(a)[2]>w*.3 for a in hc)>=3 and sum(cv2.boundingRect(a)[3]>hh*.3 for a in vc)>=3:
            result.append([x,y,x+w,y+hh])
    return result[:8]
