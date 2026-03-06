# MCP Server 実装プラン（Python / stdio / config.json対応）

## 1. 目的
- Python で **stdio 専用**の MCP Server を実装する。
- 提供ツールは `build_project` のみとする。
- `build_project` は **引数なし**で実行できるようにする。
- ビルド中は 5 秒ごとなどの一定間隔で `notifications/progress` を送信する。
- ビルド完了後は、結果とログファイルパスを含む最終レスポンスを返す。
- サーバー起動時の設定は `python server.py config.json` の `config.json` から読み込めるようにする。

最終レスポンス例:

```json
{"result": "success", "log": "build_project.log"}
```

## 2. 前提・制約
- 通信方式は stdio のみとする。
- ツールは `build_project` 1 個のみとする。
- ツール呼び出し時にビルドコマンドやログパスは受け取らない。
- ビルド設定はクライアント引数ではなく、サーバー起動時に渡す `config.json` で決定する。
- `progress` は MCP の正式仕様に従い、tool result とは別の `notifications/progress` として送信する。

## 3. 利用ライブラリ

### 外部ライブラリ
- `mcp`
	- MCP Server 実装
	- `FastMCP` によるツール定義
	- stdio トランスポート実行

### 標準ライブラリ
- `asyncio`
	- 非同期 subprocess 実行
	- 定期 progress 送信制御
- `json`
	- `config.json` の読み込み
- `sys`
	- 起動引数処理
- `dataclasses`
	- 設定値の保持
- `pathlib`
	- パス解決
- `typing`
	- 型ヒント

## 4. 設定ファイル設計

### 4.1 読み込み方式
- 起動形式は以下とする。
	- `python server.py`
	- `python server.py config.json`
- 引数なしの場合はデフォルト設定を使う。
- 引数ありの場合は指定された JSON ファイルを読み込む。

### 4.2 `config.json` で指定する項目
- `BUILD_COMMAND`
	- 実行コマンドを表す文字列配列
	- 例: `[`"python"`, `"-m"`, `"build"`]`
- `BUILD_LOG_PATH`
	- ログファイル出力先
- `PROGRESS_INTERVAL_SEC`
	- progress 通知の間隔（秒）
- `WORKING_DIR`
	- `BUILD_COMMAND` 実行時のカレントディレクトリ

### 4.3 パス解決ルール
- `BUILD_LOG_PATH` と `WORKING_DIR` が相対パスの場合、`config.json` の配置ディレクトリ基準で解決する。
- 絶対パスが指定された場合はそのまま使用する。

### 4.4 設定値の検証
- `BUILD_COMMAND` は **空でない文字列配列**であることを検証する。
- `BUILD_LOG_PATH` は空でない文字列であることを検証する。
- `PROGRESS_INTERVAL_SEC` は 0 より大きい数値であることを検証する。
- `WORKING_DIR` は空でない文字列であることを検証する。

## 5. サーバー構成

### 5.1 実装ファイル
- `server.py`
	- MCP Server 本体
	- 設定読み込み
	- `build_project` ツール実装

### 5.2 サーバーライブラリ
- `FastMCP` を使用してツールを定義する。
- `main()` で stdio トランスポート固定で起動する。

## 6. `build_project` ツール設計

### 6.1 入力
- 引数なし

### 6.2 処理フロー
1. 起動時に読み込んだ設定を参照する。
2. ログファイルの親ディレクトリを作成する。
3. ログファイルを上書きモードで開く。
4. `BUILD_COMMAND` を配列のまま `subprocess exec` 方式で起動する。
5. `WORKING_DIR` を subprocess の `cwd` に設定する。
6. ビルド開始時に progress を送信する。
7. プロセス完了まで `PROGRESS_INTERVAL_SEC` ごとに progress を送信する。
8. 終了コードを確認し、成功/失敗の最終結果を返す。

### 6.3 progress の扱い
- `ctx.report_progress(...)` を使用する。
- `progressToken` がクライアントから与えられた場合にのみ、MCP の progress 通知として配信される。
- `progress` 値は単調増加とする。
- `total` は不明のため `None` を許容する。
- `message` には `build started` や `build running... (10s)` のような文言を入れる。

### 6.4 最終レスポンス
- 成功時:

```json
{"result": "success", "log": "..."}
```

- 失敗時:

```json
{"result": "failure", "log": "...", "exit_code": 1}
```

- 例外時:

```json
{"result": "failure", "log": "...", "error": "..."}
```

## 7. ログ出力設計
- 標準出力と標準エラーは同じログファイルへ出力する。
- ログファイルは毎回上書きで開始する。
- サーバー内部例外が発生した場合は、可能ならログ末尾に追記する。
- 最終レスポンスには常にログファイルパスを含める。

## 8. エラーハンドリング
- 設定ファイルの形式不正時は、起動時に例外として扱う。
- subprocess 起動失敗時は `failure` を返す。
- 実行中例外が発生した場合、子プロセスが生きていれば停止を試みる。
- 例外情報はレスポンスの `error` に含める。

## 9. 成果物
- `server.py`
- `plan.md`
- `requirements.txt`

## 10. 検証項目
- `python server.py` でデフォルト設定起動できること。
- `python server.py config.json` で設定ファイルを読み込めること。
- `BUILD_COMMAND` が配列として正しく実行されること。
- `WORKING_DIR` が subprocess のカレントディレクトリとして反映されること。
- `build_project` が引数なしで呼べること。
- ビルド中に一定間隔で `notifications/progress` が送信されること。
- 完了時に `result` と `log` を含む最終結果が返ること。
- 失敗時に `exit_code` または `error` が返ること。
- `progressToken` がない場合でも、ビルド自体は完走し最終結果を返せること。

## 11. `config.json` 例

```json
{
	"BUILD_COMMAND": ["python", "-m", "build"],
	"BUILD_LOG_PATH": "build_project.log",
	"PROGRESS_INTERVAL_SEC": 5,
	"WORKING_DIR": "."
}
```

## 12. 実装ステップ
1. `mcp` を使った stdio MCP Server の骨組みを作る。
2. `build_project` ツールを 1 つだけ実装する。
3. progress 通知を MCP 仕様に沿って実装する。
4. `config.json` から設定を読み込めるようにする。
5. `BUILD_COMMAND` の配列実行と `WORKING_DIR` 反映を実装する。
6. ログ出力と失敗時レスポンスを整える。
7. Pylance と構文エラーを解消する。

