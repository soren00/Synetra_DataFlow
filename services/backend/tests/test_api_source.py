"""任务 2.5 验收：API 源可发起请求并返回样例数据；连通性测试返回成功/失败。"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from app.connectivity import check_connection
from app.models.connection import ConnectionType
from app.sources.rest import fetch_sample


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps([{"id": i, "name": f"item{i}"} for i in range(10)]).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


class _Server:
    def __init__(self):
        self.httpd = HTTPServer(("127.0.0.1", 0), _Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.httpd.server_address[1]}/data"

    def close(self):
        self.httpd.shutdown()


def test_fetch_sample_returns_data():
    srv = _Server()
    try:
        res = fetch_sample("GET", srv.url, limit=3)
        assert res["ok"] is True and res["status"] == 200
        assert len(res["sample"]) == 3
        assert res["sample"][0]["name"] == "item0"
    finally:
        srv.close()


def test_api_connectivity_ok():
    srv = _Server()
    try:
        ok, detail = check_connection(ConnectionType.API, {"url": srv.url, "method": "GET"}, None)
        assert ok, detail
    finally:
        srv.close()


def test_api_connectivity_unreachable():
    ok, detail = check_connection(
        ConnectionType.API, {"url": "http://127.0.0.1:1/unreachable", "method": "GET"}, None
    )
    assert not ok and "连接失败" in detail