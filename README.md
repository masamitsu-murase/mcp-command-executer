# build_python

Python で実装した stdio 専用の MCP Server です。

このサーバーは `build_project` ツールだけを提供します。`build_project` は引数を取らず、サーバー起動時に読み込んだ設定に従ってビルドコマンドを実行します。

## 機能

- stdio 専用 MCP Server
- ツールは `build_project` のみ
- ビルド実行中に一定間隔で progress 通知を送信
- 完了後に結果とログファイルパスを返却
- `config.json` からビルド設定を読込

## 必要ライブラリ

- `mcp`

インストール例:

```bash
pip install -r requirements.txt
```

## 起動方法

### デフォルト設定で起動

```bash
python server.py
```

### 設定ファイルを指定して起動

```bash
python server.py config.json
```

## config.json

指定できる項目:

- `BUILD_COMMAND`: 実行コマンドの配列
- `BUILD_LOG_PATH`: ログファイルの出力先
- `PROGRESS_INTERVAL_SEC`: progress 通知間隔（秒）
- `WORKING_DIR`: `BUILD_COMMAND` 実行時のカレントディレクトリ

例:

```json
{
  "BUILD_COMMAND": ["python", "-m", "build"],
  "BUILD_LOG_PATH": "build_project.log",
  "PROGRESS_INTERVAL_SEC": 5,
  "WORKING_DIR": "."
}
```

### パス解決ルール

- `BUILD_LOG_PATH` と `WORKING_DIR` が相対パスの場合、`config.json` が置かれているディレクトリ基準で解決されます。
- 絶対パスの場合はそのまま使われます。

## ツール仕様

### `build_project`

入力:
- なし

動作:
1. 設定済みの `BUILD_COMMAND` を実行
2. 標準出力・標準エラーを `BUILD_LOG_PATH` に保存
3. ビルド中は progress 通知を送信
4. 完了後に最終結果を返却

成功時のレスポンス例:

```json
{"result": "success", "log": "build_project.log"}
```

失敗時のレスポンス例:

```json
{"result": "failure", "log": "build_project.log", "exit_code": 1}
```

例外時のレスポンス例:

```json
{"result": "failure", "log": "build_project.log", "error": "..."}
```

## テスト

`unittest` でテストを作成しています。

実行例:

```bash
python -m unittest discover -s tests -v
```

## 主なファイル

- [server.py](server.py)
- [plan.md](plan.md)
- [tests/test_server.py](tests/test_server.py)
