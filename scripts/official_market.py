"""Free direct official data. No third-party briefing or paid API fallback."""
import datetime
import os
import xml.etree.ElementTree as ET

ECB_XML = 'https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml'
ECB_SOURCE = 'https://www.ecb.europa.eu/stats/policy_and_exchange_rates/euro_reference_exchange_rates/html/index.en.html'
OPINET_API = 'https://www.opinet.co.kr/api/avgAllPrice.do'
OPINET_SOURCE = 'https://www.opinet.co.kr/user/custapi/openApiInfoDtl.do?apiId=4'
FX = [('USD', '미국 달러', 1), ('EUR', '유로', 1), ('JPY', '일본 엔', 100),
      ('CNY', '중국 위안', 1), ('GBP', '영국 파운드', 1), ('AUD', '호주 달러', 1),
      ('CAD', '캐나다 달러', 1), ('CHF', '스위스 프랑', 1), ('HKD', '홍콩 달러', 1)]


def ecb_rates(get, now):
    response = get(ECB_XML, timeout=20)
    response.raise_for_status()
    root = ET.fromstring(response.content)
    dated = next(node for node in root.iter() if 'time' in node.attrib)
    date = datetime.date.fromisoformat(dated.attrib['time'])
    # Weekends and holidays use the last publication, with its actual date.
    if not 0 <= (now.date() - date).days <= 10:
        raise ValueError('ECB reference date is outside the accepted window')
    rates = {node.attrib['currency']: float(node.attrib['rate'])
             for node in dated if 'currency' in node.attrib}
    rates['EUR'] = 1.0
    if not rates.get('KRW') or any(value <= 0 for value in rates.values()):
        raise ValueError('Invalid ECB rate')
    return date, {code: rates['KRW'] / rates[code] * unit
                  for code, _, unit in FX if code in rates}


def build_fuelfx(get, now):
    lines = ['⛽ [기름값·환율]', f'{now:%Y년 %m월 %d일}', '']
    key = os.environ.get('OPINET_KEY', '')
    if key:
        try:
            response = get(OPINET_API, params={'certkey': key, 'out': 'json'}, timeout=20)
            response.raise_for_status()
            rows = response.json()['RESULT']['OIL']
            fuel = []
            for row in rows:
                if row.get('PRODCD') not in ('B027', 'D047'):
                    continue
                date = datetime.datetime.strptime(row['TRADE_DT'], '%Y%m%d').date()
                if not 0 <= (now.date() - date).days <= 3:
                    continue
                fuel.append(f"{row['PRODNM']} {float(row['PRICE']):,.2f}원/L "
                            f"(전일 대비 {float(row['DIFF']):+.2f}원 · {date:%Y-%m-%d} 기준)")
            lines.extend(['전국 주유소 평균가격', *fuel] if fuel else ['현재 공식 유가 자료를 확인하지 못했습니다.'])
        except Exception:
            # Never print request exceptions: query parameters contain the key.
            lines.append('현재 공식 유가 자료를 확인하지 못했습니다.')
    else:
        lines.append('기름값 수치는 무료 오피넷 인증키 연결 전까지 표시하지 않습니다.')
    lines.extend(['자료: 한국석유공사 오피넷', OPINET_SOURCE, ''])
    try:
        date, rates = ecb_rates(get, now)
        lines.extend([f'💱 유럽중앙은행 기준환율 · {date:%Y-%m-%d} 발표',
                      '유로 기준 공식 환율을 원화로 교차 계산한 참고값입니다. 은행 매매기준율이 아닙니다.', ''])
        for code, label, unit in FX:
            if code in rates:
                lines.append(f'{label} {unit}단위 : {rates[code]:,.2f}원')
    except Exception:
        lines.append('현재 유럽중앙은행 공식 환율을 확인하지 못했습니다.')
    lines.extend(['', '자료: 유럽중앙은행(ECB)', ECB_SOURCE])
    return '\n'.join(lines)
