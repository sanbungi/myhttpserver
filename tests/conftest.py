import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest
import requests

# プロジェクトルートをPythonパスに追加
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

HOST = "localhost"
PORT = 8001


@pytest.fixture(scope="session")
def server_process():
    """HTTPサーバーを起動してプロセスを管理"""
    # main.pyのあるプロジェクトディレクトリで起動
    env = os.environ.copy()
    env["PYTHONPATH"] = str(project_root)

    # サーバー起動（ポート8001を使用してテストを隔離、SSLなし）
    proc = subprocess.Popen(
        [sys.executable, str(project_root / "main.py"), "--http-port", str(PORT)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=str(project_root),
        env=env,
    )

    # サーバー起動待ち（リトライ付き）
    max_retries = 30
    for attempt in range(max_retries):
        try:
            resp = requests.get(f"http://{HOST}:{PORT}/", timeout=1)
            break
        except (
            requests.exceptions.ConnectionError,
            requests.exceptions.ConnectTimeout,
        ):
            if attempt == max_retries - 1:
                # サーバーのエラーログを出力
                try:
                    stdout, stderr = proc.communicate(timeout=1)
                    print(
                        "Server stdout:",
                        stdout.decode("utf-8", errors="replace") if stdout else "",
                    )
                    print(
                        "Server stderr:",
                        stderr.decode("utf-8", errors="replace") if stderr else "",
                    )
                except:
                    pass
                proc.terminate()
                proc.wait()
                raise RuntimeError(f"Failed to start server on {HOST}:{PORT}")
            time.sleep(0.2)

    yield proc

    # クリーンアップ
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


@pytest.fixture(scope="session")
def server(server_process):
    """HTTPサーバーのURLを提供"""
    return f"http://{HOST}:{PORT}"


@pytest.fixture
def http_socket(server_process):
    """ソケット接続を提供"""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.connect((HOST, PORT))
    s.settimeout(5)  # 5秒のタイムアウト
    yield s
    s.close()


@pytest.fixture(scope="session")
def test_files(tmp_path_factory):
    """テスト用ファイル作成"""
    root = tmp_path_factory.mktemp("webroot")
    (root / "index.html").write_text("<html>test</html>")
    (root / "test.txt").write_text("test content")
    return root
