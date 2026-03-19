"""
git履歴を解析してプロジェクトの変遷を可視化するスクリプト (読み取り専用)
Usage: python labs/analyze_git_history.py
"""

import json
import subprocess
from collections import defaultdict
from datetime import datetime


def run(cmd: str) -> str:
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    return result.stdout.strip()


def get_commits() -> list[dict]:
    raw = run('git log --all --format="%H|%cd|%s" --date=format:"%Y-%m-%d"')
    commits = []
    seen = set()
    for line in raw.splitlines():
        parts = line.split("|", 2)
        if len(parts) != 3:
            continue
        h, date, msg = parts
        if h in seen:
            continue
        seen.add(h)
        commits.append({"hash": h, "date": date, "msg": msg})
    return sorted(commits, key=lambda c: c["date"])


def get_pyproject_at(commit_hash: str) -> str:
    return run(f"git show {commit_hash}:pyproject.toml 2>/dev/null")


def get_dependency_history(commits: list[dict]) -> list[dict]:
    """各コミットでpyproject.tomlがどう変わったかを追跡"""
    history = []
    prev_deps: set[str] = set()
    checked_hashes = set()

    for c in commits:
        content = get_pyproject_at(c["hash"])
        if not content:
            continue
        in_deps = False
        deps: set[str] = set()
        for line in content.splitlines():
            if line.strip().startswith("dependencies"):
                in_deps = True
            if in_deps:
                if "]" in line:
                    in_deps = False
                stripped = line.strip().strip(",").strip('"')
                if stripped.startswith('"') or ">=" in stripped:
                    pkg = stripped.split(">=")[0].split(">")[0].strip().strip('"')
                    if pkg:
                        deps.add(pkg)

        added = deps - prev_deps
        removed = prev_deps - deps
        if (added or removed) and c["hash"] not in checked_hashes:
            checked_hashes.add(c["hash"])
            history.append(
                {
                    "date": c["date"],
                    "commit": c["hash"][:7],
                    "msg": c["msg"],
                    "added": sorted(added),
                    "removed": sorted(removed),
                    "all_deps": sorted(deps),
                }
            )
        prev_deps = deps
    return history


def categorize_commits(commits: list[dict]) -> dict:
    categories = {
        "performance": [],
        "bug_fix": [],
        "security": [],
        "testing": [],
        "refactor": [],
        "feature": [],
    }
    keywords = {
        "performance": ["高速化", "パフォーマンス", "bench", "uvloop", "pathlib", "ボトルネック", "遅い", "fast"],
        "bug_fix": ["修正", "バグ", "問題を解決", "直す", "fix", "ミス", "間違", "WIP", "待機", "デッドロック"],
        "security": ["ssl", "tls", "ban", "制限", "セキュリティ", "ip", "deny", "403", "traversal"],
        "testing": ["test", "pytest", "テスト", "カバレッジ", "coverage", "RFC"],
        "refactor": ["整理", "移動", "リファクタ", "削除", "置き換え", "format", "構造", "移植"],
        "feature": ["実装", "対応", "追加", "機能", "導入", "作成"],
    }
    for c in commits:
        msg_lower = c["msg"].lower()
        matched = False
        for cat, kws in keywords.items():
            if any(kw in msg_lower or kw in c["msg"] for kw in kws):
                categories[cat].append(c)
                matched = True
                break
        if not matched:
            categories["feature"].append(c)
    return categories


def get_activity_by_month(commits: list[dict]) -> dict:
    monthly = defaultdict(int)
    for c in commits:
        month = c["date"][:7]
        monthly[month] += 1
    return dict(sorted(monthly.items()))


def analyze_keywords_in_commits(commits: list[dict]) -> dict:
    """特定の技術的キーワードが登場するコミットを集計"""
    topics = {
        "SSL/TLS": ["ssl", "tls", "https", "証明書", "暗号"],
        "圧縮 (gzip/zstd)": ["gzip", "zstd", "compress", "圧縮"],
        "非同期 (async)": ["async", "asyncio", "uvloop", "aiofiles", "await"],
        "マルチプロセス": ["multiprocessing", "worker", "ワーカー", "プロセス", "SO_REUSEPORT"],
        "キャッシュ・ETag": ["etag", "cache", "キャッシュ", "if-none-match", "if-modified"],
        "設定ファイル": ["config", "hcl", "toml", "設定"],
        "セキュリティ": ["ban", "rate", "ip制限", "deny", "traversal", "セキュリティ"],
        "テスト": ["pytest", "test", "テスト", "rfc"],
        "パフォーマンス": ["高速化", "uvloop", "pathlib", "ボトルネック", "bench", "profile"],
    }
    result = {}
    for topic, kws in topics.items():
        matched = [c for c in commits if any(kw in c["msg"].lower() or kw in c["msg"] for kw in kws)]
        result[topic] = len(matched)
    return result


def main():
    print("=== myhttpserver git履歴解析 ===\n")

    commits = get_commits()
    print(f"総コミット数: {len(commits)}")
    print(f"期間: {commits[0]['date']} 〜 {commits[-1]['date']}\n")

    # 月別活動
    monthly = get_activity_by_month(commits)
    print("【月別コミット数】")
    for month, count in monthly.items():
        bar = "█" * (count // 2) + ("▌" if count % 2 else "")
        print(f"  {month}: {count:3d} {bar}")
    print()

    # 依存ライブラリ変遷
    print("【依存ライブラリ変遷】")
    dep_history = get_dependency_history(commits)
    for entry in dep_history:
        print(f"  [{entry['date']} {entry['commit']}] {entry['msg'][:50]}")
        if entry["added"]:
            print(f"    + 追加: {', '.join(entry['added'])}")
        if entry["removed"]:
            print(f"    - 削除: {', '.join(entry['removed'])}")
    print()

    # カテゴリ別
    cats = categorize_commits(commits)
    print("【コミットカテゴリ分類 (概算)】")
    for cat, items in cats.items():
        print(f"  {cat:15s}: {len(items):3d}件")
    print()

    # トピック別コミット数
    topics = analyze_keywords_in_commits(commits)
    print("【トピック別コミット数】")
    for topic, count in sorted(topics.items(), key=lambda x: -x[1]):
        print(f"  {topic:30s}: {count}件")
    print()

    # 重要バグ修正コミット
    bug_keywords = ["問題を解決", "間違っていて", "修正", "デッドロック", "待機してしまう", "できていなかった"]
    print("【注目バグ修正コミット】")
    for c in commits:
        if any(kw in c["msg"] for kw in bug_keywords):
            print(f"  [{c['date']}] {c['msg'][:70]}")
    print()

    # 結果をJSONで出力
    report = {
        "total_commits": len(commits),
        "period": {"start": commits[0]["date"], "end": commits[-1]["date"]},
        "monthly_activity": monthly,
        "dependency_changes": dep_history,
        "category_counts": {k: len(v) for k, v in cats.items()},
        "topic_counts": topics,
    }
    out_path = "labs/git_analysis_report.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"→ 詳細レポートを {out_path} に出力しました")


if __name__ == "__main__":
    main()
