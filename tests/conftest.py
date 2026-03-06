import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

import hcl
import pytest
import requests

# プロジェクトルートをPythonパスに追加
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

HOST = "localhost"
DEFAULT_PORT = 8001
DEFAULT_CONFIG_PATH = "test-assets/config/example.hcl"
DEFAULT_SERVER_MODE = "cli"
DEFAULT_TARGET_SERVER = "main-server"
DEFAULT_PORT_OFFSET = 1


@dataclass(frozen=True)
class ServerRuntimeConfig:
    host: str
    port: int
    command: list[str]
    mode: str
    config_path: Path | None = None


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("myhttpserver")
    group.addoption(
        "--server-mode",
        action="store",
        choices=("cli", "config-http"),
        default=DEFAULT_SERVER_MODE,
        help="Server boot mode: cli(legacy --http-port) or config-http(temp config rewrite)",
    )
    group.addoption(
        "--server-port",
        action="store",
        type=int,
        default=DEFAULT_PORT,
        help="Test server port for --server-mode=cli",
    )
    group.addoption(
        "--server-config-template",
        action="store",
        default=DEFAULT_CONFIG_PATH,
        help="Template config path used by --server-mode=config-http",
    )
    group.addoption(
        "--server-config-target",
        action="store",
        default=DEFAULT_TARGET_SERVER,
        help="Target server name for base test URL in --server-mode=config-http",
    )
    group.addoption(
        "--server-config-port-offset",
        action="store",
        type=int,
        default=DEFAULT_PORT_OFFSET,
        help="Port offset applied to every server in --server-mode=config-http",
    )


def _resolve_path_from_project(path_str: str) -> Path:
    path = Path(path_str)
    if path.is_absolute():
        return path
    return project_root / path


def _build_temp_http_config(
    template_path: Path,
    out_path: Path,
    port_offset: int,
) -> dict[str, int]:
    with template_path.open("r", encoding="utf-8") as fp:
        raw_obj = hcl.load(fp)

    server_block = raw_obj.get("server")
    if not isinstance(server_block, dict) or not server_block:
        raise RuntimeError(f"No server block found in config: {template_path}")

    rewritten_ports: dict[str, int] = {}
    for server_name, server_data in server_block.items():
        if not isinstance(server_data, dict):
            continue

        old_port = int(server_data.get("port", 80))
        new_port = old_port + port_offset
        server_data["port"] = new_port
        rewritten_ports[server_name] = new_port

        tls_data = server_data.get("tls")
        if not isinstance(tls_data, dict):
            tls_data = {}
            server_data["tls"] = tls_data
        tls_data["enabled"] = False

    out_path.write_text(json.dumps(raw_obj, indent=2), encoding="utf-8")
    return rewritten_ports


def _build_runtime_config(
    pytestconfig: pytest.Config, tmp_path_factory: pytest.TempPathFactory
) -> ServerRuntimeConfig:
    mode = pytestconfig.getoption("--server-mode")

    if mode == "cli":
        port = int(pytestconfig.getoption("--server-port"))
        return ServerRuntimeConfig(
            host=HOST,
            port=port,
            command=[
                sys.executable,
                str(project_root / "src" / "main.py"),
                "--http-port",
                str(port),
            ],
            mode=mode,
        )

    template_path = _resolve_path_from_project(
        pytestconfig.getoption("--server-config-template")
    )
    if not template_path.exists():
        raise RuntimeError(f"Config template does not exist: {template_path}")

    port_offset = int(pytestconfig.getoption("--server-config-port-offset"))
    target_server = pytestconfig.getoption("--server-config-target")

    temp_dir = tmp_path_factory.mktemp("server_config")
    generated_config_path = temp_dir / "example_test_http.hcl"
    rewritten_ports = _build_temp_http_config(
        template_path=template_path,
        out_path=generated_config_path,
        port_offset=port_offset,
    )

    if target_server not in rewritten_ports:
        available = ", ".join(sorted(rewritten_ports.keys()))
        raise RuntimeError(
            f"Server '{target_server}' was not found. Available servers: {available}"
        )

    return ServerRuntimeConfig(
        host=HOST,
        port=rewritten_ports[target_server],
        command=[
            sys.executable,
            str(project_root / "src" / "main.py"),
            "--config",
            str(generated_config_path),
        ],
        mode=mode,
        config_path=generated_config_path,
    )


def _terminate_process_group(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        return

    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            return
        proc.wait(timeout=5)


@pytest.fixture(scope="session")
def server_runtime(pytestconfig, tmp_path_factory) -> ServerRuntimeConfig:
    return _build_runtime_config(pytestconfig, tmp_path_factory)


@pytest.fixture(scope="session")
def server_process(server_runtime):
    """HTTPサーバーを起動してプロセスを管理"""
    env = os.environ.copy()
    env["PYTHONPATH"] = str(project_root)

    server_log = tempfile.NamedTemporaryFile(
        mode="w+", prefix="server_", suffix=".log", delete=False
    )

    proc = subprocess.Popen(
        server_runtime.command,
        stdout=server_log,
        stderr=server_log,
        cwd=str(project_root),
        env=env,
        start_new_session=True,
    )

    max_retries = 50
    for attempt in range(max_retries):
        try:
            requests.get(f"http://{HOST}:{server_runtime.port}/", timeout=1)
            break
        except (
            requests.exceptions.ConnectionError,
            requests.exceptions.ConnectTimeout,
        ):
            if attempt == max_retries - 1:
                _terminate_process_group(proc)
                raise RuntimeError(
                    f"Failed to start server on {HOST}:{server_runtime.port}"
                )
            time.sleep(0.2)

    yield proc

    _terminate_process_group(proc)

    server_log.flush()
    server_log.seek(0)
    log_content = server_log.read()
    server_log.close()
    os.unlink(server_log.name)
    if log_content:
        print("\n" + "=" * 60)
        print("SERVER LOG")
        print("=" * 60)
        print(log_content)
        print("=" * 60)


@pytest.fixture(scope="session")
def server(server_process, server_runtime):
    """HTTPサーバーのURLを提供"""
    return f"http://{HOST}:{server_runtime.port}"


@pytest.fixture(scope="session")
def server_port(server_runtime):
    return server_runtime.port


@pytest.fixture
def http_socket(server_process, server_runtime):
    """ソケット接続を提供"""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.connect((HOST, server_runtime.port))
    s.settimeout(5)
    yield s
    s.close()


@pytest.fixture(scope="session")
def test_files(tmp_path_factory):
    """テスト用ファイル作成"""
    root = tmp_path_factory.mktemp("webroot")
    (root / "index.html").write_text("<html>test</html>")
    (root / "test.txt").write_text("test content")
    return root
