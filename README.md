# myhttpserver

## Overview


実装は理解しやすい構造とHTTP仕様の正しさを重視しつつ、実用的な性能も維持することを目標にしています。

**主な特徴**

- asyncio + マルチコア処理による並行処理
- コアあたり約2kリクエスト/秒の処理能力
- Webサーバーとして必要な基本コンポーネントを実装
- 実際のWebサイトとして試験運用しています。
- RFC2616 / RFC7231 / RFC7232 を参考に設計

すべてのHTTP仕様を完全に満たしているわけではありませんが、RFC準拠を目標として継続的に改善を行っています。

## Demo

GIFを貼る

## Features

myhttpserver はシンプルな構造を維持しつつ、Webサーバーとして必要な基本機能を提供します。

主な機能:

- 静的ファイル配信
- リバースプロキシ
- パスベースルーティング（Longest Match）
- IPベースアクセス制御
- レートリミット
- Keep-Alive対応
- gzipレスポンス圧縮
- ETag生成
- HCLベース設定ファイル

HTTPサーバーの基本コンポーネントを理解しやすい形で実装することを重視しています。

## Quick Start

このプロジェクトでは Pythonパッケージ管理に uv を使用しています。

```bash

uv sync

# テスト用静的アセット取得
git submodule update --init --recursive

# 起動
uv run src/main.py

# テスト
uv run pytest tests --server-mode=config-http
```

## Architecture

リクエストは以下の処理フローで処理されます。

1. スレッド管理  
   - 複数リクエストを並行処理

2. TCPソケット管理  
   - 接続受付  
   - レートリミット管理

3. パケット事前検証  
   - 異常なパケットの除外

4. HTTPパース  
   - 受信データをHTTP構造に変換

5. ルーティング  
   - 静的配信またはプロキシ処理に分岐

6. アクセス制御

7. コンテンツ処理  
   - キャッシュ確認  
   - ファイル読み込み  
   - プロキシ通信

8. レスポンス生成  
   - gzip圧縮  
   - ETag生成

9. Keep-Alive判定

処理途中でエラーが発生した場合は **HTTP 500** を返します。

![server_flow](https://github.com/user-attachments/assets/1df010c1-473e-4e8c-8059-a9fc6e85f3a4)

## Configuration

myhttpserver は **HCL (HashiCorp Configuration Language)** を使用して設定を定義します。

設定は次の3階層構造で構成されています。

```
Global → Server → Route
```

| Scope | 説明 |
|------|------|
| global | サーバープロセス全体の設定 |
| server | バーチャルホスト設定 |
| route | パス単位のルーティング |

### 最小設定例

```hcl
global {
  worker_processes = 4
}

server {
  host = "localhost"
  port = 8080
  root = "./public"
}

route "/" {
  type = "static"
}
```

詳細な設定リファレンスは Wiki を参照してください。

https://github.com/sanbungi/myhttpserver/wiki

## Development

テストには **pytest** を使用しています。

```bash
uv run pytest tests --server-mode=config-http
```

## Roadmap

今後予定している機能:

- HTTP/2対応
- メトリクス機能の強化
- 負荷分散やロードバランサー機能の実験
