"""Read annual quarterly figures from PDF table cells, keeping row and column geometry."""
import re
from .pdf_extract import compact,unit_in,proof


def extract_tables(content,pages,report_period):
    import fitz
    selected=set()
    for i,text in enumerate(pages[:20]):
        if re.search(r'分季度.{0,12}(?:财务指标|财务数据|会计数据)',compact(text)):
            selected.update(j for j in (i,i+1) if j<len(pages))
    result={};carry=None;unit=None
    with fitz.open(stream=content,filetype='pdf') as pdf:
        for i in sorted(selected):
            page=pdf[i]
            for table in page.find_tables().tables:
                rows=table.extract();centers=None
                for n,row in enumerate(rows):
                    found=[]
                    for label in ('第一季度','第二季度','第三季度','第四季度'):
                        matches=[j for j,c in enumerate(row) if label in compact(c or '')]
                        if len(matches)==1:
                            rect=table.rows[n].cells[matches[0]]
                            found.append((rect[0]+rect[2])/2)
                    if len(found)==4 and len(set(found))==4:centers=found;break
                if centers is None:
                    if carry is None or carry['page']!=i-1 or table.bbox[1]>220:
                        continue
                    if abs(table.bbox[0]-carry['left'])>3 or abs(table.bbox[2]-carry['right'])>3:continue
                    centers=carry['centers']
                unit=unit_in(page.get_text(clip=(0,0,page.rect.width,table.bbox[1]))) or unit
                if unit is None:continue
                carry=dict(page=i,centers=centers,left=table.bbox[0],right=table.bbox[2])
                for n,row in enumerate(rows):
                    positions=[]
                    for center in centers:
                        matches=[j for j,rect in enumerate(table.rows[n].cells) if rect and rect[0]<=center<=rect[2]]
                        if len(matches)==1:positions.append(matches[0])
                    if len(positions)!=4 or len(set(positions))!=4:continue
                    label=compact(''.join(c or '' for c in row[:min(positions)]))
                    # MuPDF can split a wrapped label into a second physical row while
                    # retaining the numeric cells only on the first physical row.
                    for following in rows[n+1:n+3]:
                        if any(re.search(r'\d',c or '') for c in following):break
                        label+=compact(''.join(c or '' for c in following))
                    if '扣除非经常' in label and '净利润' in label:metric='core_profit'
                    elif '归属于' in label and '净利润' in label:metric='parent_profit'
                    elif label=='营业收入':metric='operating_revenue'
                    elif label=='营业总收入':metric='revenue'
                    elif '经营活动产生的现金流量净额' in label:metric='operating_cashflow'
                    else:continue
                    raw=[(row[j] or '').strip() for j in positions]
                    if not all(re.fullmatch(r'[（(]?[+\-−－]?\d[\d,]*(?:\.\d+)?[）)]?',v) for v in raw):continue
                    # Numeric/label quotes stay literal in the original text extraction.
                    for suffix,j,value in zip(('03-31','06-30','09-30','12-31'),positions,raw):
                        quote=next((line for line in pages[i].splitlines() if value in line),None)
                        if quote is None:continue
                        p=proof(value,unit,i+1,quote,'quarter_'+suffix)
                        p.update(table_label=label,bbox=list(table.rows[n].cells[j]))
                        result.setdefault(report_period[:4]+'-'+suffix,{})[metric]=p
    return result
