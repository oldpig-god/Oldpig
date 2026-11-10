"""Isolated AKShare worker: one call, parseable stdout, process deadline owned by collector."""
import contextlib
import io
import json
import sys


def main():
    import akshare as ak
    request=json.loads(sys.stdin.read())
    allowed={'stock_zh_a_hist','fund_etf_hist_em','stock_zh_a_hist_min_em','fund_etf_hist_min_em','tool_trade_date_hist_sina','stock_zh_a_spot_em','fund_etf_spot_em','stock_info_a_code_name'}
    if request['endpoint'] not in allowed:raise ValueError('Unsupported endpoint')
    with contextlib.redirect_stdout(io.StringIO()):
        frame=getattr(ak,request['endpoint'])(**request['parameters'])
    # pandas serializes missing values as null and datetimes to ISO strings.
    print(frame.to_json(orient='records',force_ascii=False,date_format='iso'))

if __name__=='__main__':main()
