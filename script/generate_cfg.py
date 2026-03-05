#!/usr/bin/env python3
"""Generate and visualize CFGs from Python source code with staticfg + graphviz."""

from __future__ import annotations

import argparse
import ast
import shutil
import sys
from pathlib import Path

from staticfg import CFGBuilder

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate CFGs from Python source files."
    )
    parser.add_argument(
        "input",
        nargs="?",
        default="src",
        help="Input Python file or directory (default: src)",
    )
    parser.add_argument(
        "--output-dir",
        default="dist/cfg",
        help="Output directory for generated CFG files (default: dist/cfg)",
    )
    parser.add_argument(
        "--format",
        default="pdf",
        help="Graphviz output format for visualization (default: pdf)",
    )
    parser.add_argument(
        "--show",
        action="store_true",
        help="Open generated files after rendering",
    )
    parser.add_argument(
        "--no-calls",
        action="store_true",
        help="Do not add call nodes to CFG",
    )
    return parser.parse_args()


def resolve_path(raw_path: str) -> Path:
    path = Path(raw_path)
    if not path.is_absolute():
        path = (PROJECT_ROOT / path).resolve()
    return path


def collect_python_files(target: Path) -> list[Path]:
    if target.is_file():
        if target.suffix != ".py":
            raise ValueError(f"Input file must be a Python file: {target}")
        return [target]

    if target.is_dir():
        return sorted(p.resolve() for p in target.rglob("*.py"))

    raise ValueError(f"Input path does not exist: {target}")


def build_cfg_name(file_path: Path) -> str:
    try:
        rel = file_path.relative_to(PROJECT_ROOT)
    except ValueError:
        rel = file_path
    return rel.as_posix().removesuffix(".py").replace("/", ".")


def output_base_path(file_path: Path, input_path: Path, output_dir: Path) -> Path:
    if input_path.is_file():
        return output_dir / file_path.stem
    rel = file_path.relative_to(input_path).with_suffix("")
    return output_dir / rel


class _StaticfgSourceNormalizer(ast.NodeTransformer):
    """Normalize AST patterns that staticfg/astor cannot stringify safely."""

    def visit_AnnAssign(self, node: ast.AnnAssign) -> ast.AST:
        self.generic_visit(node)
        # staticfg's astor path may fail when AnnAssign has no value (x: int).
        if node.value is None:
            node.value = ast.Constant(value=Ellipsis)
        return node


def load_normalized_source(file_path: Path) -> str:
    source = file_path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(file_path))
    normalized = _StaticfgSourceNormalizer().visit(tree)
    ast.fix_missing_locations(normalized)
    return ast.unparse(normalized)


class SafeCFGBuilder(CFGBuilder):
    """CFGBuilder with robust function-call name extraction."""

    def _call_name(self, node: ast.AST) -> str | None:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            base = self._call_name(node.value)
            return f"{base}.{node.attr}" if base else node.attr
        if isinstance(node, ast.Subscript):
            return self._call_name(node.value)
        if isinstance(node, ast.Call):
            return self._call_name(node.func)
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        return None

    def visit_Call(self, node: ast.Call) -> None:
        func_name = self._call_name(node.func)
        if func_name:
            self.current_block.func_calls.append(func_name)

    def new_functionCFG(self, node: ast.FunctionDef, asynchr: bool = False) -> None:
        self.current_id += 1
        func_body = ast.Module(body=node.body, type_ignores=[])
        func_builder = type(self)()
        self.cfg.functioncfgs[node.name] = func_builder.build(
            node.name, func_body, asynchr, self.current_id
        )
        self.current_id = func_builder.current_id + 1


def generate_cfg(
    file_path: Path,
    cfg_name: str,
    output_base: Path,
    fmt: str,
    calls: bool,
    show: bool,
) -> None:
    source = load_normalized_source(file_path)
    cfg = SafeCFGBuilder().build_from_src(cfg_name, source)
    output_base.parent.mkdir(parents=True, exist_ok=True)

    # staticfg returns a graphviz.Digraph; DOT text can be emitted without dot binary.
    graph = cfg._build_visual(format=fmt, calls=calls)  # noqa: SLF001
    dot_path = output_base.with_suffix(".dot")
    dot_path.write_text(graph.source, encoding="utf-8")

    if fmt == "dot":
        return

    if shutil.which("dot") is None:
        raise RuntimeError(
            "Graphviz 'dot' executable is not found on PATH. "
            "Install Graphviz to render image outputs."
        )

    graph.format = fmt
    graph.render(
        filename=output_base.name,
        directory=str(output_base.parent),
        view=show,
        cleanup=True,
    )


def main() -> int:
    args = parse_args()
    calls = not args.no_calls

    input_path = resolve_path(args.input)
    output_dir = resolve_path(args.output_dir)

    if args.format != "dot" and shutil.which("dot") is None:
        print(
            "[ERROR] Graphviz 'dot' executable is not found on PATH. "
            "Install Graphviz to render image/PDF outputs.",
            file=sys.stderr,
        )
        return 2

    try:
        files = collect_python_files(input_path)
    except ValueError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 2

    if not files:
        print(f"[ERROR] No Python files found under: {input_path}", file=sys.stderr)
        return 2

    failures: list[tuple[Path, Exception]] = []
    print(f"[*] Generating CFG for {len(files)} file(s) ...")
    for file_path in files:
        cfg_name = build_cfg_name(file_path)
        output_base = output_base_path(file_path, input_path, output_dir)
        try:
            generate_cfg(file_path, cfg_name, output_base, args.format, calls, args.show)
            print(
                f"[OK] {file_path} -> {output_base}.dot"
                + ("" if args.format == "dot" else f", {output_base}.{args.format}")
            )
        except Exception as exc:  # noqa: BLE001
            failures.append((file_path, exc))
            print(f"[NG] {file_path}: {exc}", file=sys.stderr)

    if failures:
        print(
            f"[ERROR] Failed to generate CFG for {len(failures)} file(s).",
            file=sys.stderr,
        )
        return 1

    print(f"[*] Done. Output directory: {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
