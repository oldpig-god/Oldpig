"""Read deducted parent profit from labelled financial-summary table columns."""
import re
from .pdf_extract import compact,unit_in


def extract_core_pdf(content,pages,report_period):
    import fitz
    numeric=re.compile(r'[（(]?[+\-−－]?\d[\d,]*(?:\.\d+)?[）)]?')
    q3=report_period.endswith('-09-30');year=report_period[:4]
    with fitz.open(stream=content,filetype='pdf') as pdf:
        for i,text in enumerate(pages[:20]):
            if '扣除' not in compact(text):continue
            for table in pdf[i].find_tables().tables:
                rows=table.extract();centers={}
                for n,row in enumerate(rows):
                    for j,value in enumerate(row):
                        label=compact(value or '')
                        if not label:continue
                        rect=table.rows[n].cells[j]
                        if '年初至' in label and '期末' in label and not any(v in label for v in ('比上年','增减')):
                            centers['ytd']=(rect[0]+rect[2])/2
                        half_year_header=(report_period.endswith('-06-30') and
                            re.fullmatch(r'(?:本报告期|报告期)(?:[（(]1[—－–−至~～-]6月[）)])?',label))
                        if label in ('本报告期','本期','本年度','本年',year+'年',year+'年度') or half_year_header:
                            centers['current']=(rect[0]+rect[2])/2
                center=centers.get('ytd' if q3 else 'current')
                if center is None:continue
                for n,row in enumerate(rows):
                    positions=[j for j,rect in enumerate(table.rows[n].cells) if rect and rect[0]<=center<=rect[2]]
                    if len(positions)!=1:continue
                    column=positions[0];raw=compact(row[column] or '')
                    if not numeric.fullmatch(raw):continue
                    label_rows=[n]
                    for prior in range(n-1,max(-1,n-3),-1):
                        if any(numeric.fullmatch(compact(c or '')) for c in rows[prior]):break
                        label_rows.insert(0,prior)
                    for following in range(n+1,min(n+3,len(rows))):
                        if any(numeric.fullmatch(compact(c or '')) for c in rows[following]):break
                        label_rows.append(following)
                    first_amount=next(j for j,c in enumerate(row) if numeric.fullmatch(compact(c or '')))
                    left=table.rows[n].cells[first_amount][0];boxes=[]
                    for rn in label_rows:
                        boxes += [list(rect) for j,rect in enumerate(table.rows[rn].cells)
                                  if rect and rect[2]<=left+1 and rows[rn][j]]
                    label=compact(''.join(pdf[i].get_textbox(rect) for rect in boxes))
                    if not ('扣除非经常' in label and '净利润' in label):continue
                    if any(v in label for v in ('每股','收益率','主营业务')):continue
                    inline=re.search(r'[（(](百万元|千元|万元|亿元|元)[）)]',label)
                    unit=inline[1] if inline else unit_in(pdf[i].get_text(clip=(0,0,pdf[i].rect.width,table.bbox[1])))
                    if unit is None:continue
                    def cell(j,column_name):
                        rect=table.rows[n].cells[j];quote=pdf[i].get_textbox(rect);value=compact(quote)
                        if not numeric.fullmatch(value):return None
                        return dict(raw_value=value,unit=unit,page=i+1,quote=quote,column=column_name,
                                    bbox=list(rect),label_boxes=boxes,label_text=label,extraction='pdf_cell')
                    result=cell(column,'current_ytd')
                    if result is None:continue
                    if q3 and 'current' in centers:
                        qc=[j for j,rect in enumerate(table.rows[n].cells) if rect and rect[0]<=centers['current']<=rect[2]]
                        if len(qc)==1:
                            quarter=cell(qc[0],'current_quarter')
                            if quarter:result['quarter_proof']=quarter
                    return result
    return None


def verify_cell(content,p):
    import fitz
    with fitz.open(stream=content,filetype='pdf') as pdf:
        page=pdf[p['page']-1];text=page.get_textbox(p['bbox'])
        label=compact(''.join(page.get_textbox(rect) for rect in p['label_boxes']))
        if text!=p['quote'] or compact(text)!=p['raw_value'] or label!=p['label_text']:
            raise ValueError('财务单元格或字段名称与原始PDF不一致')
        if 'quarter_proof' in p:verify_cell(content,p['quarter_proof'])
