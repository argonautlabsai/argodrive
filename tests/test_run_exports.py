"""Recent CSV exports select across blocks without losing engine settings."""
import csv
from datetime import datetime, timedelta, timezone
import http.client
import io
import threading
import unittest
from unittest.mock import patch
from test_monitor import dashboard


NOW = datetime(2026, 9, 12, 12).timestamp()


def arm(name, age, **extra):
    return {'arm': name, 'ran': datetime.fromtimestamp(NOW - age * 3600).isoformat(' '),
            'tok_s_steady': 4.2, 'set': {'req_threads': 48}, **extra}


def rows(blob):
    return list(csv.DictReader(io.StringIO(blob.decode())))


class RunExportTests(unittest.TestCase):
    def setUp(self):
        self.blocks = [
            {'block': 'new-folder', 'rows': [arm('old', 25), arm('boundary', 1),
                                           {'arm': 'undated'}, {'arm': 'bad-date', 'ran': 'invalid'}]},
            {'block': 'old-folder', 'rows': [arm('recent', .5, incomplete=True), arm('now', 0),
                                           arm('future', -1), arm('six', 6), arm('day', 24)]},
        ]
        self.mock = patch.object(dashboard, '_stats_blocks', return_value=self.blocks)
        self.mock.start()
        self.addCleanup(self.mock.stop)

    def test_hours_use_rolling_time_inclusive_boundaries_not_folder_names(self):
        for hours, expected in [(1, ['now', 'recent', 'boundary']),
                                (3, ['now', 'recent', 'boundary']),
                                (6, ['now', 'recent', 'boundary', 'six']),
                                (24, ['now', 'recent', 'boundary', 'six', 'day'])]:
            exported = rows(dashboard.stats_csv(hours=hours, now=NOW))
            self.assertEqual([r['arm'] for r in exported], expected)
            self.assertEqual(exported[1]['incomplete'], 'True')
            self.assertEqual(exported[1]['req_threads'], '48')
            self.assertEqual(exported[1]['block'], 'old-folder')

    def test_recent_count_sorts_globally_and_keeps_full_settings_union(self):
        self.blocks = [{'block': 'z', 'rows': [arm(str(n), n) for n in range(25)]},
                       {'block': 'a', 'rows': [arm('tie', 0, set={'cfg_special': 'comma, newline\nvalue'})]}]
        self.mock.target._stats_blocks.return_value = self.blocks
        for count in (1, 5, 10, 20):
            result = rows(dashboard.stats_csv(last_runs=count, now=NOW))
            self.assertEqual(len(result), count)
            self.assertEqual(result[0]['arm'], 'tie')
            self.assertEqual(result[0]['cfg_special'], 'comma, newline\nvalue')
            if count > 1:
                self.assertEqual(result[1]['arm'], '0')
                self.assertEqual(result[1]['req_threads'], '48')
        self.assertNotIn('block', self.blocks[0]['rows'][0])  # Shared stats cache stays unchanged.

    def test_recent_count_with_fewer_rows_and_all_preserves_undated(self):
        self.assertEqual(len(rows(dashboard.stats_csv(last_runs=20))), 7)
        all_rows = rows(dashboard.stats_csv())
        self.assertEqual(len(all_rows), 9)
        self.assertEqual(next(r for r in all_rows if r['arm'] == 'undated')['ran'], '(no mtime)')

    def test_offset_timestamp_and_empty_range(self):
        stamp = datetime.fromtimestamp(NOW, timezone.utc) - timedelta(minutes=30)
        self.mock.target._stats_blocks.return_value = [{'block': 'offset', 'rows': [{'arm': 'utc', 'ran': stamp.isoformat()}]}]
        self.assertEqual(rows(dashboard.stats_csv(hours=1, now=NOW))[0]['arm'], 'utc')
        self.assertTrue(dashboard.stats_csv(hours=1, now=NOW + 86400).startswith(b'# no arms'))

    def test_existing_date_and_block_export_still_preserve_settings(self):
        result = rows(dashboard.stats_csv('2026-09-12', '2026-09-12', 'old-folder'))
        self.assertEqual({r['arm'] for r in result}, {'recent', 'now', 'future', 'six'})
        self.assertTrue(all(r['req_threads'] == '48' for r in result))

    def test_http_filters_filename_and_invalid_requests_never_export_all(self):
        server = dashboard.http.server.ThreadingHTTPServer(('127.0.0.1', 0), dashboard.H)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        def get(path):
            connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=5)
            connection.request('GET', path)
            response = connection.getresponse()
            result = response.status, dict(response.getheaders()), response.read()
            connection.close()
            return result
        try:
            with patch.object(dashboard.time, 'time', return_value=NOW):
                status, headers, body = get('/runs-export.csv?hours=1')
                self.assertEqual(status, 200)
                self.assertIn('last-1-hours_exported-2026-09-12_12-00-00.csv', headers['Content-Disposition'])
                self.assertEqual(len(rows(body)), 3)
                status, headers, body = get('/runs-export.csv?last=5')
                self.assertEqual(status, 200)
                self.assertEqual(len(rows(body)), 5)
                self.assertIn('last-5-runs', headers['Content-Disposition'])
                self.assertEqual(len(rows(get('/stats.csv')[2])), 9)
                for query in ('', 'hours=', 'hours=0', 'hours=nan', 'last=-1', 'last=100',
                              'hours=1&last=5', 'hours=1&hours=24', 'scope=all'):
                    self.assertEqual(get('/runs-export.csv?' + query)[0], 400, query)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == '__main__':
    unittest.main()
