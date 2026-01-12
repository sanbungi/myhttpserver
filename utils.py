class HTTPRequest:
    def __init__(self, method, path, version, headers, body):
        self.method = method
        self.path = path
        self.version = version
        self.headers = headers
        self.body = body

    def __repr__(self):
        headers_str = "\n".join(f"    {k}: {v}" for k, v in self.headers.items())
        return f"HTTPRequest(\n    method={self.method},\n    path={self.path},\n    version={self.version},\n    headers={{\n{headers_str}\n    }},\n    body={repr(self.body)}\n)"


def parse_request(request_text: str) -> HTTPRequest:
    lines = request_text.split("\r\n")

    # Request Line parsing
    request_line = lines[0]
    method, path, version = request_line.split(" ")

    headers = {}
    i = 1
    while i < len(lines):
        line = lines[i]
        if line == "":
            i += 1
            break
        if ": " in line:
            key, value = line.split(": ", 1)
            headers[key] = value
        i += 1

    body = "\r\n".join(lines[i:])

    return HTTPRequest(method, path, version, headers, body)
