# myhttpserver

## Hot to Run

パッケージ管理はuvを使用している

```bash
// 環境作成
uv sync

// 起動
uv run main.py

// テスト
uv run pytest .
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
