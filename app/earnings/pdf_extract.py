"""Extract current columns from consolidated Chinese financial statement layouts.
Unrecognized layouts remain in the document inventory for cell review.
"""
import re
from .schema import DataError, UNITS

NUMBER=re.compile(r'[（(]?[-−－]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?[）)]?')
LABELS={
 'revenue':r'(?:一、)?营业总收入', 'operating_revenue':r'(?:其中[:：])?营业收入',
 'parent_profit':r'(?:1[.、．])?归属于母公司(?:股东|所有者)的净利润',
 'net_profit':r'(?:五、|四、)?净利润', 'total_profit':r'(?:四、|三、)?利润总额',
 'operating_profit':r'(?:三、)?营业利润','minority_profit':r'(?:2[.、．])?少数股东损益',
 'operating_cost':r'(?:其中[:：])?营业成本','total_operating_cost':r'(?:二、)?营业总成本',
 'selling_expense':r'销售费用','administrative_expense':r'管理费用',
 'research_expense':r'研发费用','finance_expense':r'财务费用',
 'credit_impairment':r'信用减值损失','asset_impairment':r'资产减值损失',
 'income_tax':r'(?:减[:：])?所得税费用',
 'assets':r'资产总计','liabilities':r'负债合计','cash':r'货币资金',
 'inventory':r'存货','receivables':r'应收账款','contract_liabilities':r'合同负债',
 'parent_equity':r'归属于母公司(?:所有者|股东)权益(?:合计)?',
 'minority_equity':r'少数股东权益', 'total_equity':r'所有者权益(?:（或股东权益）)?合计',
 'operating_cashflow':r'经营活动产生的现金流量净额',
 'capital_expenditure':r'购建固定资产、无形资产和其他长期资产支',
}


def pages_from_pdf(content):
    import fitz
    with fitz.open(stream=content,filetype='pdf') as document:
        return [p.get_text(sort=True) for p in document]


def compact(text):return re.sub(r'\s+','',text)


def unit_in(text):
    matches=re.findall(r'(?:单位\s*[：:]|金额单位(?:均)?为|货币单位为)\s*(?:人民币)?\s*(百万元|千元|万元|亿元|元)',text)
    return matches[-1] if matches else None


def cells(text):
    return [m.group() for m in NUMBER.finditer(text)]


def proof(raw,unit,page,quote,column):
    return dict(raw_value=raw,unit=unit,page=page,quote=quote,column=column)


def extract(pages,report_period):
    facts={};issues=[];section=None;statement_unit=None
    for index,text in enumerate(pages):
        lines=text.splitlines()
        for n,line in enumerate(lines):
            label=compact(line)
            heading=re.fullmatch(r'(?:(?:20\d{2}年.*)|(?:[（(]?[0-9一二三四五六七八九十]+[）)、.．]))?(合并资产负债表|合并利润表|合并现金流量表|母公司资产负债表|母公司利润表|母公司现金流量表)(?:[（(]续[）)])?',label)
            if heading:
                section=heading[1] if heading[1].startswith('合并') else None
                statement_unit=unit_in('\n'.join(lines[n:n+12])) or statement_unit
            if not section or not statement_unit:continue
            for metric,pattern in LABELS.items():
                if metric in facts:continue
                for span in (1,2,3):
                    row='\n'.join(lines[n:n+span])
                    match=re.match(r'^'+pattern,compact(row))
                    if not match:continue
                    if span>1 and match.end()<=len(compact(line)):
                        following=[compact(v) for v in lines[n+1:n+span] if compact(v)]
                        if any(re.fullmatch(r'\d+/\d+',v) or (re.match(r'^[\u4e00-\u9fff]',v) and not v.startswith(('付的现金','现金','号填列'))) for v in following):continue
                    end=match.end();position=0
                    for offset,char in enumerate(row):
                        if not char.isspace():position+=1
                        if position==end:break
                    suffix=row[offset+1:]
                    suffix=re.sub(r'[一二三四五六七八九十]+[、,]\d+','',suffix)
                    values=cells(suffix)
                    if len(values)!=2:continue
                    facts[metric]=proof(values[0],statement_unit,index+1,row,'current_ytd' if section!='合并资产负债表' else 'current_period_end')
                    break
    # Deducted parent profit appears in the key financial-data table, not the income statement.
    summary_unit=None
    for index,text in enumerate(pages[:10]):
        lines=text.splitlines()
        if '分季度主要财务指标' in compact(text) or all(x in compact(text) for x in ['第一季度','第四季度']):continue
        for n,line in enumerate(lines):
            summary_unit=unit_in(line) or summary_unit
            if re.search(r'(?:营业收入|净利润)[（(]元[）)]',compact(line)):summary_unit='元'
            if '扣除' not in compact(line) and not ('除非经常' in compact(line) and '归属于上市公司股东的扣' in compact(''.join(lines[max(0,n-3):n]))):continue
            u='元' if re.search(r'净利润[（(]元[）)]',compact(''.join(lines[max(0,n-2):n+4]))) else summary_unit
            if not u:continue
            context=compact('\n'.join(lines[max(0,n-1):n+4]))
            if '每股' in compact(line) or '收益率' in compact(line):continue
            candidates=[]
            if compact(line).startswith('除非经常') and n>0 and len(cells(lines[n-1]))>=3:
                candidates=[(index,n-1,lines[n-1])]
            for j in range(n,min(n+3,len(lines))):
                if len(cells(lines[j]))>=3:candidates.append((index,j,lines[j]))
            if not candidates and index>0 and compact(line).startswith('股东的扣除非'):
                previous=pages[index-1].splitlines()
                candidates=[(index-1,j,v) for j,v in enumerate(previous)
                    if compact(v).startswith('归属于上市公司') and len(cells(v))>=4][-1:]
            if not candidates:continue
            pi,li,row=candidates[0];values=cells(row)
            if report_period.endswith('-09-30'):
                column={4:2,8:4}.get(len(values))
                if column is None:continue
            else:column=0
            facts['core_profit']=dict(proof(values[column],u,pi+1,row,'current_ytd'),label_page=index+1,label_quote=line)
            break
        if 'core_profit' in facts:break
    if 'core_profit' not in facts:issues.append('扣非利润单元格需要核对')
    for name in ('assets','revenue','parent_profit','operating_cashflow'):
        if name not in facts:issues.append(name+' 未提取')
    return facts,issues


def reviewed_cells(pages, specifications):
    """Explicit page/row/column overrides for layouts reviewed against the PDF."""
    result={}
    for name,item in specifications.items():
        page=item['page'];quote=item['quote'];raw=item['raw_value']
        if quote not in pages[page-1] or raw not in quote:
            raise DataError('复核单元格未匹配原文：'+name)
        result[name]=proof(raw,item['unit'],page,quote,item['column'])
    return result
