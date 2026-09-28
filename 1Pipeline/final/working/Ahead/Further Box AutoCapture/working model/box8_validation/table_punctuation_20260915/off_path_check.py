"""Confirm the table toggle does not alter normal OCR or retain a colour copy OFF."""
import json,sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import numpy as np
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
import pipeline_cli_box8 as new
source=json.loads((ROOT/'paragraph_test_outputs/capture_cli_006_20260915_120623_box8.json').read_text())
engine=new.load_ocr_engine('english')
with patch.object(new,'_GUI_CONTROLLER',None):
    baseline=new.run_ocr(engine,source['image'],False,None)
def essential(regions):
    return [{k:r.get(k) for k in ('region_id','region_type','bbox','source_text')} for r in regions]
off=SimpleNamespace(table_enabled=False,table_source_image='stale')
with patch.object(new,'_GUI_CONTROLLER',SimpleNamespace(box8=off)):
    candidate=new.run_ocr(engine,source['image'],False,None)
assert baseline[0]==candidate[0] and essential(baseline[1])==essential(candidate[1]) and np.array_equal(baseline[2],candidate[2])
assert off.table_source_image is None
on=SimpleNamespace(table_enabled=True,table_source_image=None)
with patch.object(new,'_GUI_CONTROLLER',SimpleNamespace(box8=on)):
    enabled=new.run_ocr(engine,source['image'],False,None)
assert baseline[0]==enabled[0] and essential(baseline[1])==essential(enabled[1]) and np.array_equal(baseline[2],enabled[2])
assert on.table_source_image is not None and on.table_source_image.shape==candidate[2].shape
print('TABLE_OFF_CORE_OCR_IDENTICAL; TABLE_SOURCE_COPY_ONLY_WHEN_ON')
