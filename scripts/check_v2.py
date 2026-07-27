with open('D:/leanpython/daily_stock_analysis/paper_trading/market_listener_v2.py', 'r', encoding='utf-8') as f:
    content = f.read()

print('_check_dynamic_sltp:', '_check_dynamic_sltp' in content)
print('sltp_dynamic_threshold_pct:', 'sltp_dynamic_threshold_pct' in content)
print('is_market_open_now:', 'is_market_open_now' in content)
print('def __init__:', 'def __init__' in content)
print('Total lines:', len(content.split('\n')))