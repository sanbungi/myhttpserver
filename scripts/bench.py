#!/usr/bin/env python3
"""
wrk2 を使った HTTP サーバー E2E ベンチマークスクリプト。

使い方:
    python script/bench.py                    # デフォルト設定で実行
    python script/bench.py --duration 30      # 30秒実行
    python script/bench.py --rate 5000        # 5000 req/s でテスト
    python script/bench.py --report-dir results/  # レポート保存先を指定
    python script/bench.py --skip-build       # wrk2 の再ビルドをスキップ

レポートは JSON 形式で保存され、CI で成果物として保存可能。
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import signal
import subprocess
import sys
import textwrap
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# 定数
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
WRK2_REPO = "https://github.com/giltene/wrk2.git"
WRK2_DIR = PROJECT_ROOT / ".wrk2"
WRK2_BIN = WRK2_DIR / "wrk"

SERVER_HOST = "localhost"
DEFAULT_PORT = 8099  # テスト用ポート (他テストと衝突しないように)

# デフォルトベンチマーク設定
DEFAULT_THREADS = 2
DEFAULT_CONNECTIONS = 10
DEFAULT_DURATION = 10  # 秒
DEFAULT_RATE = 200  # req/s


# ---------------------------------------------------------------------------
# データクラス
# ---------------------------------------------------------------------------
@dataclass
class BenchmarkScenario:
    """ベンチマークシナリオ定義"""

    name: str
    path: str
    method: str = "GET"
    headers: dict[str, str] = field(default_factory=dict)
    description: str = ""


@dataclass
class LatencyStats:
    avg: str = ""
    stdev: str = ""
    max: str = ""
    percentile_50: str = ""
    percentile_75: str = ""
    percentile_90: str = ""
    percentile_99: str = ""
    percentile_99_9: str = ""
    percentile_99_99: str = ""
    percentile_100: str = ""


@dataclass
class BenchmarkResult:
    scenario: str
    description: str
    threads: int
    connections: int
    duration_sec: int
    target_rate: int
    actual_rate: float = 0.0
    total_requests: int = 0
    total_bytes: str = ""
    latency: LatencyStats = field(default_factory=LatencyStats)
    errors_connect: int = 0
    errors_read: int = 0
    errors_write: int = 0
    errors_timeout: int = 0
    errors_http: int = 0
    raw_output: str = ""


@dataclass
class BenchmarkReport:
    timestamp: str
    server_info: dict[str, Any] = field(default_factory=dict)
    system_info: dict[str, Any] = field(default_factory=dict)
    config: dict[str, Any] = field(default_factory=dict)
    results: list[dict[str, Any]] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# wrk2 ビルド
# ---------------------------------------------------------------------------
def install_wrk2(force: bool = False) -> Path:
    """wrk2 を git clone してビルドする。"""
    if WRK2_BIN.exists() and not force:
        print(f"[*] wrk2 already built: {WRK2_BIN}")
        return WRK2_BIN

    print("[*] Installing wrk2 from source …")

    if WRK2_DIR.exists():
        shutil.rmtree(WRK2_DIR)

    subprocess.run(
        ["git", "clone", "--depth=1", WRK2_REPO, str(WRK2_DIR)],
        check=True,
        capture_output=True,
        text=True,
    )
    print("[*] git clone done.")

    cpu_count = os.cpu_count() or 2
    subprocess.run(
        ["make", f"-j{cpu_count}"],
        cwd=str(WRK2_DIR),
        check=True,
        capture_output=True,
        text=True,
    )
    print(f"[*] wrk2 built successfully: {WRK2_BIN}")

    if not WRK2_BIN.exists():
        raise FileNotFoundError(f"wrk2 binary not found after build: {WRK2_BIN}")

    return WRK2_BIN


# ---------------------------------------------------------------------------
# サーバー管理
# ---------------------------------------------------------------------------
def start_server(port: int) -> subprocess.Popen:
    """テスト用にサーバーを起動する。"""
    venv_python = PROJECT_ROOT / ".venv" / "bin" / "python"
    python = str(venv_python) if venv_python.exists() else sys.executable

    cmd = [
        python,
        str(PROJECT_ROOT / "src" / "main.py"),
        "--http-port",
        str(port),
    ]
    print(f"[*] Starting server: {' '.join(cmd)}")

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        cwd=str(PROJECT_ROOT),
    )

    # 起動待ち
    import urllib.request
    import urllib.error

    url = f"http://{SERVER_HOST}:{port}/"
    for attempt in range(50):
        try:
            urllib.request.urlopen(url, timeout=1)
            break
        except (urllib.error.URLError, ConnectionError, OSError):
            if proc.poll() is not None:
                raise RuntimeError(
                    f"Server exited unexpectedly (returncode={proc.returncode})."
                )
            time.sleep(0.2)
    else:
        proc.terminate()
        proc.wait()
        raise RuntimeError(f"Server did not start within timeout on port {port}")

    print(f"[*] Server is ready on port {port} (pid={proc.pid})")
    return proc


def stop_server(proc: subprocess.Popen) -> None:
    """サーバーをグレースフルに停止する。"""
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
    print("[*] Server stopped.")


# ---------------------------------------------------------------------------
# wrk2 実行 & パース
# ---------------------------------------------------------------------------
def run_wrk2(
    wrk_bin: Path,
    url: str,
    threads: int,
    connections: int,
    duration: int,
    rate: int,
    headers: dict[str, str] | None = None,
) -> str:
    """wrk2 を実行して stdout を返す。"""
    cmd = [
        str(wrk_bin),
        "-t", str(threads),
        "-c", str(connections),
        "-d", f"{duration}s",
        "-R", str(rate),
        "--latency",
        url,
    ]
    if headers:
        for k, v in headers.items():
            cmd.extend(["-H", f"{k}: {v}"])

    print(f"[*] Running: {' '.join(cmd)}")
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=duration + 30,
    )
    if result.returncode != 0 and result.stderr:
        print(f"[!] wrk2 stderr: {result.stderr}", file=sys.stderr)
    return result.stdout


def parse_wrk2_output(raw: str) -> dict[str, Any]:
    """wrk2 の出力をパースして辞書を返す。"""
    data: dict[str, Any] = {}

    # Latency Distribution (HdrHistogram)
    lat = {}
    # パーセンタイル行: 50.000%    1.23ms
    pct_pattern = re.compile(r"^\s+([\d.]+)%\s+([\d.]+\w+)\s*$", re.MULTILINE)
    for m in pct_pattern.finditer(raw):
        pct_val = float(m.group(1))
        if pct_val == 50.0:
            lat["percentile_50"] = m.group(2)
        elif pct_val == 75.0:
            lat["percentile_75"] = m.group(2)
        elif pct_val == 90.0:
            lat["percentile_90"] = m.group(2)
        elif pct_val == 99.0:
            lat["percentile_99"] = m.group(2)
        elif pct_val == 99.9:
            lat["percentile_99_9"] = m.group(2)
        elif pct_val == 99.99:
            lat["percentile_99_99"] = m.group(2)
        elif pct_val == 100.0:
            lat["percentile_100"] = m.group(2)

    # Latency avg/stdev/max 行
    lat_line = re.search(
        r"Latency\s+([\d.]+\w+)\s+([\d.]+\w+)\s+([\d.]+\w+)", raw
    )
    if lat_line:
        lat["avg"] = lat_line.group(1)
        lat["stdev"] = lat_line.group(2)
        lat["max"] = lat_line.group(3)

    data["latency"] = lat

    # Requests/sec
    rps = re.search(r"Requests/sec:\s+([\d.]+)", raw)
    if rps:
        data["actual_rate"] = float(rps.group(1))

    # Total requests & transfer
    req_match = re.search(r"(\d+)\s+requests in", raw)
    if req_match:
        data["total_requests"] = int(req_match.group(1))

    transfer = re.search(r"([\d.]+\w+)\s+read", raw)
    if transfer:
        data["total_bytes"] = transfer.group(1)

    # Socket errors
    err_match = re.search(
        r"Socket errors:\s+connect\s+(\d+),\s+read\s+(\d+),\s+write\s+(\d+),\s+timeout\s+(\d+)",
        raw,
    )
    if err_match:
        data["errors_connect"] = int(err_match.group(1))
        data["errors_read"] = int(err_match.group(2))
        data["errors_write"] = int(err_match.group(3))
        data["errors_timeout"] = int(err_match.group(4))

    # Non-2xx/3xx
    http_err = re.search(r"Non-2xx or 3xx responses:\s+(\d+)", raw)
    if http_err:
        data["errors_http"] = int(http_err.group(1))

    return data


# ---------------------------------------------------------------------------
# シナリオ定義
# ---------------------------------------------------------------------------
def default_scenarios() -> list[BenchmarkScenario]:
    """デフォルトのベンチマークシナリオ一覧。"""
    return [
        BenchmarkScenario(
            name="",
            path="/",
            description="静的 HTML ファイル (index.html)",
        ),
        BenchmarkScenario(
            name="small_text",
            path="/test.txt",
            description="小さなテキストファイル",
        ),
        BenchmarkScenario(
            name="not_found",
            path="/nonexistent_path_404",
            description="404 Not Found レスポンス",
        ),
        BenchmarkScenario(
            name="keep_alive",
            path="/",
            headers={"Connection": "keep-alive"},
            description="Keep-Alive 接続での静的 HTML",
        ),
        BenchmarkScenario(
            name="gzip_compressed",
            path="/",
            headers={"Accept-Encoding": "gzip"},
            description="gzip 圧縮リクエスト",
        ),
    ]


# ---------------------------------------------------------------------------
# メイン処理
# ---------------------------------------------------------------------------
def run_benchmark(
    wrk_bin: Path,
    port: int,
    scenarios: list[BenchmarkScenario],
    threads: int,
    connections: int,
    duration: int,
    rate: int,
) -> list[BenchmarkResult]:
    """全シナリオのベンチマークを実行する。"""
    results: list[BenchmarkResult] = []
    base_url = f"http://{SERVER_HOST}:{port}"

    for i, scenario in enumerate(scenarios, 1):
        print(f"\n{'='*60}")
        print(f"[{i}/{len(scenarios)}] {scenario.name}: {scenario.description}")
        print(f"{'='*60}")

        url = f"{base_url}{scenario.path}"
        raw = run_wrk2(wrk_bin, url, threads, connections, duration, rate, scenario.headers)
        print(raw)

        parsed = parse_wrk2_output(raw)

        br = BenchmarkResult(
            scenario=scenario.name,
            description=scenario.description,
            threads=threads,
            connections=connections,
            duration_sec=duration,
            target_rate=rate,
            actual_rate=parsed.get("actual_rate", 0.0),
            total_requests=parsed.get("total_requests", 0),
            total_bytes=parsed.get("total_bytes", ""),
            latency=LatencyStats(**parsed.get("latency", {})),
            errors_connect=parsed.get("errors_connect", 0),
            errors_read=parsed.get("errors_read", 0),
            errors_write=parsed.get("errors_write", 0),
            errors_timeout=parsed.get("errors_timeout", 0),
            errors_http=parsed.get("errors_http", 0),
            raw_output=raw,
        )
        results.append(br)

        # シナリオ間で少し待機
        time.sleep(1)

    return results


def build_report(
    results: list[BenchmarkResult],
    threads: int,
    connections: int,
    duration: int,
    rate: int,
    port: int,
) -> BenchmarkReport:
    """結果をレポートにまとめる。"""
    now = datetime.now(timezone.utc)

    # Python / OS 情報
    system_info = {
        "os": platform.system(),
        "os_release": platform.release(),
        "arch": platform.machine(),
        "cpu_count": os.cpu_count(),
        "python_version": platform.python_version(),
    }

    config = {
        "threads": threads,
        "connections": connections,
        "duration_sec": duration,
        "target_rate": rate,
        "port": port,
    }

    # サマリ計算
    successful = [r for r in results if r.errors_http == 0]
    summary = {
        "total_scenarios": len(results),
        "successful_scenarios": len(successful),
        "failed_scenarios": len(results) - len(successful),
        "avg_actual_rate": (
            round(sum(r.actual_rate for r in results) / len(results), 2)
            if results
            else 0
        ),
    }

    report = BenchmarkReport(
        timestamp=now.isoformat(),
        server_info={"host": SERVER_HOST, "port": port},
        system_info=system_info,
        config=config,
        results=[asdict(r) for r in results],
        summary=summary,
    )
    return report


def save_report(report: BenchmarkReport, report_dir: Path) -> Path:
    """レポートを JSON ファイルに保存する。"""
    report_dir.mkdir(parents=True, exist_ok=True)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"bench_report_{ts}.json"
    filepath = report_dir / filename

    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(asdict(report), f, ensure_ascii=False, indent=2)

    print(f"\n[*] Report saved: {filepath}")

    # latest シンボリックリンクを更新
    latest = report_dir / "bench_report_latest.json"
    if latest.exists() or latest.is_symlink():
        latest.unlink()
    latest.symlink_to(filename)

    return filepath


def print_summary(report: BenchmarkReport) -> None:
    """コンソールにサマリを表示する。"""
    print(f"\n{'='*70}")
    print("BENCHMARK SUMMARY")
    print(f"{'='*70}")
    print(f"  Timestamp    : {report.timestamp}")
    print(f"  System       : {report.system_info.get('os', '?')} "
          f"{report.system_info.get('arch', '?')} "
          f"({report.system_info.get('cpu_count', '?')} CPUs)")
    print(f"  Config       : {report.config.get('threads')}t / "
          f"{report.config.get('connections')}c / "
          f"{report.config.get('duration_sec')}s / "
          f"{report.config.get('target_rate')} req/s target")
    print(f"{'─'*70}")

    for r in report.results:
        lat = r.get("latency", {})
        total_errors = sum([
            r.get("errors_connect", 0),
            r.get("errors_read", 0),
            r.get("errors_write", 0),
            r.get("errors_timeout", 0),
        ])
        status = "OK" if r.get("errors_http", 0) == 0 and total_errors == 0 else "WARN"
        print(f"\n  [{status}] {r['scenario']}: {r['description']}")
        print(f"       Rate     : {r.get('actual_rate', 0):.2f} req/s "
              f"(target: {r.get('target_rate', 0)})")
        print(f"       Latency  : avg={lat.get('avg', 'N/A')}, "
              f"p99={lat.get('percentile_99', 'N/A')}, "
              f"max={lat.get('max', 'N/A')}")
        print(f"       Requests : {r.get('total_requests', 0)}, "
              f"Transfer: {r.get('total_bytes', 'N/A')}")
        if total_errors > 0 or r.get("errors_http", 0) > 0:
            print(f"       Errors   : socket={total_errors}, http={r.get('errors_http', 0)}")

    print(f"\n{'─'*70}")
    s = report.summary
    print(f"  Scenarios: {s['total_scenarios']} total, "
          f"{s['successful_scenarios']} ok, "
          f"{s['failed_scenarios']} with errors")
    print(f"  Avg Rate : {s['avg_actual_rate']} req/s")
    print(f"{'='*70}")


def check_regression(report: BenchmarkReport, threshold_rate: float | None) -> bool:
    """CI 用: 目標レートに対して実測が閾値を下回っていたら失敗を返す。"""
    if threshold_rate is None:
        return True  # チェックなし → 常に成功

    for r in report.results:
        actual = r.get("actual_rate", 0)
        if actual < threshold_rate:
            print(
                f"[FAIL] {r['scenario']}: actual_rate={actual:.2f} < threshold={threshold_rate}",
                file=sys.stderr,
            )
            return False
    return True


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def parse_cli_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="wrk2 E2E Benchmark for myhttpserver",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            例:
              python script/bench.py
              python script/bench.py --duration 30 --rate 5000
              python script/bench.py --report-dir artifacts/bench
              python script/bench.py --threshold-rate 500  # CI 回帰チェック
        """),
    )
    parser.add_argument(
        "--port", type=int, default=DEFAULT_PORT,
        help=f"サーバーポート (default: {DEFAULT_PORT})",
    )
    parser.add_argument(
        "--threads", "-t", type=int, default=DEFAULT_THREADS,
        help=f"wrk2 スレッド数 (default: {DEFAULT_THREADS})",
    )
    parser.add_argument(
        "--connections", "-c", type=int, default=DEFAULT_CONNECTIONS,
        help=f"同時接続数 (default: {DEFAULT_CONNECTIONS})",
    )
    parser.add_argument(
        "--duration", "-d", type=int, default=DEFAULT_DURATION,
        help=f"テスト時間[秒] (default: {DEFAULT_DURATION})",
    )
    parser.add_argument(
        "--rate", "-R", type=int, default=DEFAULT_RATE,
        help=f"ターゲットリクエストレート[req/s] (default: {DEFAULT_RATE})",
    )
    parser.add_argument(
        "--report-dir", type=str, default=str(PROJECT_ROOT / "bench_reports"),
        help="レポート保存ディレクトリ",
    )
    parser.add_argument(
        "--skip-build", action="store_true",
        help="wrk2 の再ビルドをスキップ",
    )
    parser.add_argument(
        "--force-build", action="store_true",
        help="wrk2 を強制的に再ビルド",
    )
    parser.add_argument(
        "--no-server", action="store_true",
        help="サーバーを自動起動しない (既に起動済みの場合)",
    )
    parser.add_argument(
        "--threshold-rate", type=float, default=None,
        help="CI 回帰チェック: 全シナリオの実測レートがこの値以上であることを要求",
    )
    parser.add_argument(
        "--json-stdout", action="store_true",
        help="JSON レポートを stdout にも出力する (CI パイプライン向け)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_cli_args()

    print(f"{'='*70}")
    print("  myhttpserver — wrk2 E2E Benchmark")
    print(f"{'='*70}")

    # --- wrk2 ビルド ---
    if args.skip_build and WRK2_BIN.exists():
        wrk_bin = WRK2_BIN
        print(f"[*] Using existing wrk2: {wrk_bin}")
    else:
        wrk_bin = install_wrk2(force=args.force_build)

    # --- サーバー起動 ---
    server_proc = None
    if not args.no_server:
        server_proc = start_server(args.port)

    exit_code = 0
    try:
        # --- ベンチマーク実行 ---
        scenarios = default_scenarios()
        results = run_benchmark(
            wrk_bin=wrk_bin,
            port=args.port,
            scenarios=scenarios,
            threads=args.threads,
            connections=args.connections,
            duration=args.duration,
            rate=args.rate,
        )

        # --- レポート生成 ---
        report = build_report(
            results=results,
            threads=args.threads,
            connections=args.connections,
            duration=args.duration,
            rate=args.rate,
            port=args.port,
        )

        print_summary(report)

        report_path = save_report(report, Path(args.report_dir))

        if args.json_stdout:
            print(json.dumps(asdict(report), ensure_ascii=False, indent=2))

        # --- 回帰チェック ---
        if not check_regression(report, args.threshold_rate):
            print("[FAIL] Performance regression detected!", file=sys.stderr)
            exit_code = 1
        else:
            print("[PASS] Benchmark completed successfully.")

    except KeyboardInterrupt:
        print("\n[!] Interrupted by user.")
        exit_code = 130
    except Exception as e:
        print(f"[!] Benchmark failed: {e}", file=sys.stderr)
        exit_code = 1
    finally:
        if server_proc:
            stop_server(server_proc)

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
