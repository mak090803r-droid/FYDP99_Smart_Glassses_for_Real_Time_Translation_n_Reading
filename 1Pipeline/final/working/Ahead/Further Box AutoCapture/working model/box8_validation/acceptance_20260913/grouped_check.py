"""Confirm table Q&A with the real production paragraph grouping, no new cases."""
import json
import validate as v
import pipeline_cli_box8 as host

report = json.loads((v.OUT / 'results.json').read_text(encoding='utf-8'))
before = v.hashes()
for table in report['tables']:
    regions = host.group_ocr_lines_into_paragraphs(host.merge_ocr_fragments_into_lines(table['ocr_fragments']), (800,1300,3))
    table['production_grouped_regions'] = regions
    f = v.feature(regions, table['tables'])
    updated = []
    for old in table['questions']:
        q = old['question']
        required = ['12'] if 'Processor?' in q else ['Radio','4'] if 'highest Current' in q and table['name']=='ruled' else ['Translation','0.75'] if 'lowest Delay' in q else ['2.50'] if 'Delay of OCR' in q else []
        updated.append(v.question(f, q, old['expected'], required, not required))
    table['initial_fragment_context_questions'] = table['questions']
    table['questions'] = updated
    f.cancel()
    (v.OUT/'results.json').write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf-8')
report['production_unchanged'] = report['production_unchanged'] and before == v.hashes()
(v.OUT/'results.json').write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf-8')
print('GROUPED_CONTEXT_CHECK_COMPLETE')
