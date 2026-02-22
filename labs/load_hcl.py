import hcl
from config_model import AppConfig  # 上記で作成したクラスをimport

# ファイル読み込み
with open("example.hcl", "r") as fp:
    raw_obj = hcl.load(fp)

# クラスインスタンスへ変換
config = AppConfig.load(raw_obj)

# 検証: オブジェクトとしてアクセス可能
print(f"Global Workers: {config.global_settings.worker_processes}")

for server in config.servers:
    print(f"--- Server: {server.name} ({server.host}:{server.port}) ---")

    # Header設定へのアクセス
    if server.headers:
        print(f"  Remove Headers: {server.headers.remove}")

    # Routeへのアクセス
    for route in server.routes:
        print(f"  Route [{route.path}] Type: {route.type}")
        if route.security:
            print(f"    > IP Allow: {route.security.ip_allow}")
        if route.backend:
            print(f"    > Proxy to: {route.backend.upstream}")
