# myhttpserver

## How to Run

パッケージ管理はuvを使用している

```bash
# 環境作成
uv sync

# 起動
uv run src/main.py

# テスト
uv run pytest .
```

## Install as CLI (`myhttpserver`)

```bash
# wheel作成
uv build

# wheelをインストール
pip install dist/myhttpserver-0.0.1-py3-none-any.whl

# CLIで起動
myhttpserver --webroot ./html --config ./config/example.hcl --port 8080
```

## 処理フロー

- ソケット生成・スレッド管理
- TCPレベルのやり取り
	- 異常なパケットを確認
- HTTPとしてパース
- 検証
- ルーティング
- 正常なレスポンス生成

途中で失敗すれば即座に500エラーを返す。


# 設定ファイル解説（仮）

MyHTTPServerは、HCL (HashiCorp Configuration Language) を採用した設定ファイル（`config.hcl`）を使用します。設定は **Global > Server > Route** の3層構造で記述します。

## 1. 階層構造

| スコープ | 記述数 | 役割 |
| --- | --- | --- |
| **`global`** | 1つのみ | プロセス全体の動作（スレッド、ログ、共通タイムアウト） |
| **`server`** | 複数可 | バーチャルホストの設定（ドメイン、ポート、TLS、共通ヘッダー） |
| **`route`** | 複数可 | パスごとの挙動（静的配信、プロキシ、リダイレクト、制限） |

---

## 2. Global ブロック

サーバー全体の設定を定義します。

```hcl
global {
  worker_processes  = 4      # ワーカースレッド数
  max_connections   = 1024   # 最大同時接続数
  timeout           = "30s"  # 通信タイムアウト

  logging {
    level  = "info"          # debug, info, warn, error
    output = "stdout"        # パスまたは stdout
    format = "json"          # json または text
  }
}

```

---

## 3. Server ブロック

特定のホスト名やポートに対する設定を定義します。

### 基本設定

| パラメータ | 型 | 説明 |
| --- | --- | --- |
| `host` | string | 待ち受けるホスト名（ドメイン） |
| `port` | int | 待ち受けるポート番号 |
| `root` | string | ドキュメントルートのパス |

### サブブロック

* **`tls`**: HTTPS有効化時に `enabled`, `cert`, `key` を指定。
* **`headers`**: `add` (map), `remove` (list) を使用してレスポンスヘッダーを操作。

---

## 4. Route ブロック

URLパスごとの挙動を定義します。**最長一致（Longest Match）**の原則で適用されます。

### Route Type 一覧

| type | 必須ブロック | 用途 |
| --- | --- | --- |
| **`static`** | なし | `root`配下の静的ファイル配信 |
| **`proxy`** | `backend` | リバースプロキシ（`upstream`への転送） |
| **`raw`** | `respond` | 固定ステータスコードとボディの返却 |
| **`redirect`** | `redirect` | 指定URLへのHTTPリダイレクト |

### 設定例

```hcl
# 静的配信 + アクセス制限
route "/admin" {
  type = "static"
  security {
    ip_allow = ["192.168.1.0/24"]
    deny_all = true
  }
}

# リバースプロキシ
route "/api" {
  type = "proxy"
  backend {
    upstream = "http://localhost:9000"
    timeout  = "30s"
  }
}

# 固定レスポンス (メンテナンス等)
route "/health" {
  type = "raw"
  respond {
    status = 200
    body   = "OK"
  }
}

# リダイレクト
route "/old-path" {
  type = "redirect"
  redirect {
    url  = "/new-path"
    code = 301
  }
}

```

---

## 5. 共通オプション

`route` 内で利用可能な補助設定です。

### security ブロック

* `ip_allow`: 許可するIP/CIDRのリスト。
* `deny_all`: trueの場合、リスト外からのアクセスを403で拒否。

### headers ブロック

* `add` / `set`: ヘッダーの追加・上書き。 `{ "Key" = "Value" }` 形式。
* `remove`: 削除するヘッダー名のリスト。

---
