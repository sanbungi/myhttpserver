# myhttpserver

[![Build Wheel](https://github.com/sanbungi/myhttpserver/actions/workflows/build-wheel.yml/badge.svg)](https://github.com/sanbungi/myhttpserver/actions/workflows/build-wheel.yml)
[![Pytest](https://github.com/sanbungi/myhttpserver/actions/workflows/pytest.yml/badge.svg)](https://github.com/sanbungi/myhttpserver/actions/workflows/pytest.yml)
[![Better Stack Badge](https://uptime.betterstack.com/status-badges/v1/monitor/2gwju.svg)](https://uptime.betterstack.com/?utm_source=status_badge)

## Overview


実装は理解しやすい構造とHTTP仕様の正しさを重視しつつ、実用的な性能も維持することを目標にしています。

**主な特徴**

- asyncio + マルチコア処理による並行処理
- コアあたり約2kリクエスト/秒の処理能力
- Webサーバーとして必要な基本コンポーネントを実装
- 実際のWebサイトとして[試験運用](myhttp.nanora.work/)しています。
- RFC2616 / RFC7231 / RFC7232 を参考に設計

すべてのHTTP仕様を完全に満たしているわけではありませんが、RFC準拠を目標として継続的に改善を行っています。

## Demo

https://github.com/user-attachments/assets/1bb80d72-59de-4145-9ac2-824f96ec124c

試験運用Webサイトのメトリクス公開してます。↓↓↓

https://myhttp.betteruptime.com/

## Features

シンプルな構造を維持しつつ、Webサーバーとして必要な基本機能を提供します。

主な機能:

- [x] 静的ファイル配信
- [x] リバースプロキシ
- [x] パスベースルーティング（トライ木）
- [x] IPベースアクセス制御
- [x] Keep-Alive対応
- [x] gzipレスポンス圧縮
- [x] ETag生成
- [x] HCLベース設定ファイル
- [ ] Cookieの受け渡し、キャッシュ無効処理
- [ ] ユーザー情報など、キャッシュされてはいけない場合があるため、詳細なテストを実施
- [ ] ストリーミングによる分割レスポンス
- [ ] ルールベースのレート制限


HTTPサーバーの基本コンポーネントを理解しやすい形で実装することを重視しています。

## Quick Start

このプロジェクトでは Pythonパッケージ管理に uv を使用しています。

```bash
# 未インストールの場合
curl -LsSf https://astral.sh/uv/install.sh | sh

git clone https://github.com/sanbungi/myhttpserver.git
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

![server_flow_yoko](https://github.com/user-attachments/assets/c151b35f-fe0e-4942-b9ad-67d329fcdd17)

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
