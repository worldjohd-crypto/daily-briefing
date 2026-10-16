import datetime
import unittest

from official_transactions import CSV, CASE_LIMIT, _parse_csv, build_transactions


HEADER = '"NO","시군구","단지명","전용면적(㎡)","계약년월","계약일","거래금액(만원)","층","해제사유발생일"\n'


def export(*rows):
    return ('"국토교통부 공개자료 안내"\n' + HEADER + '\n'.join(rows)).encode('cp949')


class OfficialTransactionsTests(unittest.TestCase):
    def test_parse_excludes_cancelled_stale_bad_and_duplicate_cases(self):
        raw = export(
            '"1","부산광역시 해운대구 우동","검증단지","84.90","202610","16","50,000","10","-"',
            '"2","서울특별시 강남구","취소단지","84.9","202610","17","80,000","3","20261017"',
            '"3","경기도 수원시","오래된단지","84.9","202610","01","10,000","3","-"',
            '"4","부산광역시 해운대구 우동","검증단지","84.90","202610","16","50,000","10","-"',
            '"5","서울특별시 강남구","오류단지","NaN","202610","17","80,000","3","-"',
        )
        rows = _parse_csv(raw, datetime.date(2026, 10, 11), datetime.date(2026, 10, 17))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][2], '검증단지')
        self.assertEqual(rows[0][4], 50000)

    def test_live_builder_uses_one_official_csv_and_marks_contract_scope(self):
        calls = []
        raw = export('"1","부산광역시 해운대구 우동","검증단지","84.90","202610","16","50,000","10","-"')

        class Reply:
            cookies = {}
            content = raw
            def raise_for_status(self):
                pass
            def json(self):
                return {'cnt': 1}

        def get(url, **kwargs):
            calls.append((url, kwargs))
            return Reply()

        text = build_transactions(get, datetime.datetime(2026, 10, 17, 3, 0))
        self.assertEqual(sum(url == CSV for url, _ in calls), 1)
        self.assertTrue(all(url.startswith('https://rt.molit.go.kr/') for url, _ in calls))
        self.assertIn('50,000만원', text)
        self.assertIn('전용 84.9㎡', text)
        self.assertIn('신규 등록 순위가 아닙니다', text)
        self.assertEqual(calls[-1][1]['params']['srhFromDt'], '2026-10-11')

    def test_access_failure_does_not_retry_or_show_fabricated_rows(self):
        calls = []
        def get(url, **kwargs):
            calls.append(url)
            raise ValueError('quota/access/network unavailable')
        text = build_transactions(get, datetime.datetime(2026, 10, 17))
        self.assertIn('확인하지 못했습니다', text)
        self.assertIn('https://rt.molit.go.kr', text)
        self.assertEqual(len(calls), 1)


if __name__ == '__main__':
    unittest.main()
