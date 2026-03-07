def build_error_page_html(status: int, reason: str):
    template = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>{CODE} {REASON}</title>
  <style>
    body{{
      margin: 40px;
    }}
  </style>
</head>
<body>
  <h1>{CODE} {REASON}</h1>
  <hr>
  <address>MyHTTPServer/0.1</address>
</body>
</html>"""
    return template.format(CODE=status, REASON=reason).lstrip()
