from http.server import BaseHTTPRequestHandler, HTTPServer


class DebugHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self._print_request()
        self._send_response()

    def do_POST(self):
        self._print_request()
        self._send_response()

    def _print_request(self):
        print(f"\n--- Request received from {self.client_address} ---")
        print(f"{self.command} {self.path} {self.request_version}")
        print(self.headers)

        # ボディがある場合は表示
        content_len = int(self.headers.get("Content-Length", 0))
        if content_len > 0:
            body = self.rfile.read(content_len)
            print(f"Body: {body.decode('utf-8', errors='replace')}")

    def _send_response(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self.end_headers()
        self.wfile.write(b"OK: Request received by Debug Server")


# ポート8080で起動
httpd = HTTPServer(("0.0.0.0", 1234), DebugHandler)
print("Debug server listening on port 1234...")
httpd.serve_forever()
