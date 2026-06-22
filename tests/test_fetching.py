import http.client
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import stock_report


class FakeResponse:
    def __init__(self, payload: bytes | None = None, error: Exception | None = None):
        self.payload = payload
        self.error = error

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self):
        if self.error:
            raise self.error
        return self.payload


class FetchJsonTests(unittest.TestCase):
    def test_retries_incomplete_download_and_uses_tpex_referer(self):
        payload = json.dumps([{"Code": "2330"}]).encode("utf-8")
        incomplete = http.client.IncompleteRead(b"{", 10)

        with tempfile.TemporaryDirectory() as temp_dir:
            with (
                mock.patch.object(stock_report, "CACHE_DIR", Path(temp_dir)),
                mock.patch.object(stock_report, "FETCH_RETRIES", 2),
                mock.patch.object(stock_report.time, "sleep"),
                mock.patch.object(
                    stock_report.urllib.request,
                    "urlopen",
                    side_effect=[
                        FakeResponse(error=incomplete),
                        FakeResponse(payload=payload),
                    ],
                ) as urlopen,
            ):
                result = stock_report.fetch_json(
                    "https://www.tpex.org.tw/openapi/v1/example",
                    "example.json",
                )

        self.assertEqual(result, [{"Code": "2330"}])
        self.assertEqual(urlopen.call_count, 2)
        request = urlopen.call_args_list[0].args[0]
        self.assertEqual(request.get_header("Referer"), "https://www.tpex.org.tw/")


if __name__ == "__main__":
    unittest.main()
