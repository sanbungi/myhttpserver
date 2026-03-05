# Agent Guide for myhttpserver

## Purpose
- Repository for implementing and validating the custom HTTP server `myhttpserver`.
- Goal: incrementally add and test HTTP/1.1 features, incl. static serving, proxying, redirects, cache control, Range, and compression.

## Dev Setup
- Use `uv` for package management and execution.
- Common commands:
  - Sync env: `uv sync`
  - Start server: `uv run src/main.py --host 0.0.0.0 --config config/example.hcl`
  - Run tests: `uv run pytest tests/ --server-mode=config-http`

## Project Layout
- `src/main.py`: Entry point; loads HCL config, initializes logging, starts worker processes.
- `src/server/`: Core server implementation.
- `tests/`: Basic/advanced tests based on `pytest`.
- `config/example.hcl`: Server definitions (`main-server`, `redirect-server`, etc.).
- `script/`: Dev helper scripts (debug/bench).
- `html/`: Static content for serving tests.
- `labs/`: Experimental snippets for new features; typically ignore.

## Core Files
- `src/main.py`
  - Parses CLI args (`--config`, `--port`, `--http-port`, `--host`).
  - Loads `AppConfig`; determines startup mode.
- `src/server/core.py`
  - Owns socket creation and `asyncio.start_server` startup.
  - Loads TLS certs when TLS is enabled.
- `src/server/worker.py`
  - Per-connection loop: parse request, validate, send response.
  - Primary Keep-Alive and access-log handling.
- `src/server/protocol.py`
  - `HTTPRequest` / `HTTPResponse` models and HTTP message parse/build logic.
- `src/server/router.py`
  - Route resolution, static serving, proxy/raw/redirect branching, conditional-request handling.
- `src/server/config_model.py`
  - Normalizes HCL into `AppConfig`, `ServerConfig`, etc.
- `src/server/FileCache.py`
  - In-memory file-content cache to reduce I/O.
- `tests/conftest.py`
  - Test server bootstrap + fixtures: `server`, `server_port`, `http_socket`.

## Server Mode in Tests
- Test server startup is managed in `tests/conftest.py`.
- Default to `config-http` mode (closest to production-like config).
- Use `cli` mode only for quick checks with minimal config.
- Frequently used options:
  - `--server-mode`: `config-http` / `cli`
  - `--server-config-template`: HCL template path (default: `config/example.hcl`)
  - `--server-config-target`: target `server` name for base URL (default: `main-server`)
  - `--server-config-port-offset`: port-collision offset (default: `1`)
- Typical runs:
  - `uv run pytest tests/basic -q --server-mode=config-http`
  - `uv run pytest tests/advanced -q --server-mode=config-http`

## HCL Config Overview
- Project config is written in HCL (HashiCorp Configuration Language).
- Human-readable structured format centered on `global { ... }` and `server "name" { ... }`.
- `global`: shared settings (worker count, timeouts, logging, etc.).
- `server`: listener port, TLS, and `route` blocks (`static` / `proxy` / `redirect` / `raw`).
- Keys/spec may change; treat `config/example.hcl` as source of truth.

## Implementation Rules (Perf/Maintainability)
- No heavy work per request.
- Specifically avoid:
  - Per-request class creation, dynamic definitions, expensive initialization.
  - Per-request config reload, logger reconfiguration, large-object regeneration.
  - Recompiling identical regexes or rebuilding high-cost structures each time.
- Policy:
  - Prepare reusable objects at process start or module load.
  - Minimize allocations and I/O on hot paths (`handle_client`, `parse_request`, `resolve_route`).
  - Use caches (e.g., `FileCache`, metadata caches).

## Minimum Verification After Changes
- Run target tests first, then broader tests if needed:
  - `uv run pytest tests/basic -q --server-mode=config-http`
  - `uv run pytest tests/advanced -q --server-mode=config-http`
