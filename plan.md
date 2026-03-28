# MCP Server 実装プラン（Python / stdio / YAML / ツール引数対応）

## 1. 目的
- Python で **stdio 専用**の MCP Server を実装する。
- サーバー起動時に YAML 設定ファイルを必須で読み込み、複数のツールを登録できるようにする。
- 各ツールは YAML で定義した `input_schema` に従って **引数を受け取れる**ようにする。
- 実行コマンドは YAML の `command` 配列で定義し、`${param_name}` 形式のプレースホルダでツール入力を埋め込めるようにする。
- 実行中は一定間隔で `notifications/progress` を送信する。
- 完了後は結果とログファイルパスを返す。

成功時レスポンス例:

```json
{"result": "success", "log": "build_log.txt"}
```

## 2. 前提・制約
- 通信方式は stdio のみとする。
- 実行内容のベースはサーバー起動時に読み込んだ YAML 設定で決定する。
- `progress` は MCP の `notifications/progress` として送信する。
- デフォルト設定は持たない。
- ツール名は任意の文字列を許容する。
- v1 では **シンプルさ優先**とし、動的引数は `command` 配列中の `${name}` 置換のみをサポートする。
- v1 では `command` 要素内の **部分文字列置換**はサポートしない。
  - OK: `${target}`
  - NG: `prefix-${target}`
- v1 では optional 引数の自動省略ロジックは持たない。
  - `command` から参照する入力項目は、基本的に `input_schema.required` に含める前提とする。
- シェル展開は行わず、`subprocess exec` 用の argv 配列として実行する。

## 3. 利用ライブラリ

### 外部ライブラリ
- `mcp`
  - MCP Server 実装
  - `FastMCP` によるツール定義
  - stdio トランスポート実行
- `PyYAML`
  - YAML 設定ファイルの読み込み

### 標準ライブラリ
- `asyncio`
  - 非同期 subprocess 実行
  - 定期 progress 送信制御
- `sys`
  - 起動引数処理
- `dataclasses`
  - 設定値の保持
- `pathlib`
  - パス解決
- `typing`
  - 型ヒント
- `re`
  - プレースホルダ `${name}` の判定

## 4. 設定ファイル設計

### 4.1 読み込み方式
- 起動形式は以下とする。
  - `python server.py config.yaml`
- 設定ファイル引数は必須とする。
- 指定された YAML ファイルを読み込む。

### 4.2 `config.yaml` で指定する項目
- `progress_interval_sec`
  - progress 通知の間隔（秒）
- `tools`
  - 実行可能なツール定義の配列
  - 各要素は以下を持つ
    - `name`
    - `description`
    - `command`
    - `log_path`
    - `working_dir`
    - `input_schema`（任意。引数付きツールで使用）

例:

```yaml
progress_interval_sec: 5
tools:
  - name: "run_partial_test"
    description: "Run selected tests."
    input_schema:
      type: object
      additionalProperties: false
      properties:
        target:
          type: string
          description: "pytest node id or file path"
        keyword:
          type: string
          description: "pytest -k expression"
      required:
        - target
        - keyword
    command: ["python", "-m", "pytest", "-k", "${keyword}", "${target}"]
    log_path: "logs/run_partial_test.log"
    working_dir: "."

  - name: "run_all_project_tests"
    description: "Run all tests in this project."
    command: ["python", "-m", "unittest", "discover", "-s", "tests", "-v"]
    log_path: "logs/test_project.log"
    working_dir: "."
```

### 4.3 `input_schema` の仕様
- JSON Schema 風のシンプルな object 定義を受け付ける。
- v1 でサポート対象とする項目:
  - `type: object`
  - `properties`
  - `required`
  - `additionalProperties`
- `properties` の各入力項目は、少なくとも以下の型を許可する。
  - `string`
  - `integer`
  - `number`
  - `boolean`
- `additionalProperties: false` を推奨し、未知の入力を拒否できるようにする。
- `input_schema` 未指定の場合は **引数なしツール**として扱う。

### 4.4 `command` の仕様
- `command` は **最終的な argv 配列**を表す非空配列とする。
- 各要素は文字列のみを許可する。
- 各要素が `${name}` に完全一致する場合、その要素はツール入力 `name` の値で置換する。
- プレースホルダではない要素は固定文字列としてそのまま使用する。
- 置換後の値は文字列化して argv に格納する。

例:

```yaml
command: ["python", "-m", "pytest", "-k", "${keyword}", "${target}"]
```

入力:

```json
{"target": "tests/test_server.py", "keyword": "load_config"}
```

生成される argv:

```json
["python", "-m", "pytest", "-k", "load_config", "tests/test_server.py"]
```

### 4.5 コマンド決定ルール
- すべてのツールは YAML 内の `command` で実行コマンドを明示する。
- ツール名によるコマンド自動決定は行わない。
- `${name}` の解決先は、そのツールの入力値のみとする。
- `command` 内で参照した `name` は `input_schema.properties` に定義されている必要がある。

### 4.6 パス解決ルール
- `log_path` と `working_dir` が相対パスの場合、`config.yaml` の配置ディレクトリ基準で解決する。
- 絶対パスの場合はそのまま使用する。
- 本対応では `log_path`・`working_dir`・`command` の部分文字列置換は扱わない。

### 4.7 設定値の検証
- `progress_interval_sec` は 0 より大きい数値であることを検証する。
- `progress_interval_sec` は必須とする。
- `tools` は空でない配列であることを検証する。
- `name`、`description`、`log_path`、`working_dir` は必須かつ空でない文字列であることを検証する。
- `command` は必須かつ空でない文字列配列であることを検証する。
- ツール名の重複を禁止する。
- `input_schema` を指定する場合は、mapping であることを検証する。
- `input_schema.type` は `object` のみ許可する。
- `input_schema.properties` は mapping、`required` は文字列配列であることを検証する。
- `command` に `${name}` が含まれる場合、`name` が `input_schema.properties` に存在することを検証する。

## 5. サーバー構成

### 5.1 実装ファイル
- `server.py`
  - MCP Server 本体
  - YAML 設定読み込み
  - ツール登録
  - 入力検証
  - プレースホルダ展開
  - コマンド実行

### 5.2 内部データ構造
- `ToolConfig` に以下を追加する。
  - `input_schema: dict[str, Any] | None`
- `command` は展開前のテンプレートとして保持する。
- プレースホルダ判定用の正規表現を用意する。
  - 例: `^\$\{([A-Za-z_][A-Za-z0-9_]*)\}$`

### 5.3 ツール登録方式
- `FastMCP.add_tool()` を用いて設定に応じて動的にツールを登録する。
- ツール呼び出し時に入力値を受け取れる形に変更する。
- 登録時に `input_schema` を MCP クライアントへ提示できるようにする。
- `input_schema` 未指定のツールは従来どおり引数なしで呼べる形を維持する。

## 6. ツール実行設計

### 6.1 入力
- 引数なしツール:
  - 入力なし
- 引数ありツール:
  - `input_schema` に従う object 入力

### 6.2 処理フロー
1. ツール設定から `command`・`log_path`・`working_dir`・`input_schema` を取得する。
2. 受け取った入力値を `input_schema` に照らして検証する。
3. `command` 配列を走査し、`${name}` を入力値で置換して実行用 argv を組み立てる。
4. ログファイルの親ディレクトリを作成する。
5. ログファイルを上書きモードで開く。
6. 展開後の argv を `subprocess exec` 方式で起動する。
7. `working_dir` を `cwd` に設定する。
8. 開始時に progress を送信する。
9. プロセス完了まで `progress_interval_sec` ごとに progress を送信する。
10. 終了コードに応じて成功/失敗レスポンスを返す。

### 6.3 入力検証の方針
- `required` に定義された項目が未指定ならエラーにする。
- `additionalProperties: false` の場合、未定義キーを拒否する。
- 値の型が `properties` の定義と一致しない場合はエラーにする。
- `command` で参照した項目が実行時入力に存在しない場合はエラーにする。

### 6.4 progress の扱い
- `ctx.report_progress(...)` を使用する。
- `progress` 値は単調増加とする。
- `total` は不明のため `None` とする。
- `message` には `<tool_name> started` や `<tool_name> running... (10s)` のような文言を入れる。

### 6.5 最終レスポンス
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

※ v1 ではレスポンスに展開後コマンドや入力値は含めない。

## 7. ログ出力設計
- 標準出力と標準エラーは同じログファイルへ出力する。
- ログファイルは毎回上書きで開始する。
- サーバー内部例外が発生した場合は、可能ならログ末尾に追記する。
- 最終レスポンスには常にログファイルパスを含める。

## 8. エラーハンドリング
- YAML の形式不正や必須項目不足は起動時に例外として扱う。
- `input_schema` の形式不正は起動時に例外として扱う。
- `command` のプレースホルダ参照不正は起動時に例外として扱う。
- 実行時入力不足や型不一致はツール呼び出しエラーとして扱う。
- subprocess 起動失敗時は `failure` を返す。
- 実行中例外が発生した場合、子プロセスが生きていれば停止を試みる。
- 例外情報はレスポンスの `error` に含める。

## 9. テスト方針
- YAML 読み込みと相対パス解決を検証する。
- 引数なしツールの後方互換を検証する。
- `input_schema` 読み込みと保持を検証する。
- `command` の `${name}` 参照が `input_schema.properties` と整合することを検証する。
- 未定義プレースホルダを起動時エラーにすることを検証する。
- `required` 入力不足を実行時エラーにすることを検証する。
- 型不一致入力を実行時エラーにすることを検証する。
- プレースホルダ展開後の argv が期待どおりになることを検証する。
- 動的ツール登録を検証する。
- コマンド成功・失敗・起動例外時のレスポンスを検証する。

## 10. 成果物
- `server.py`
- `plan.md`
- `tests/test_server.py`
- 必要に応じて `sample.yml`
- 必要に応じて `README.md`

## 11. 実装ステップ
1. `ToolConfig` と設定読み込み処理に `input_schema` を追加する。
2. `input_schema` の最小バリデーションを実装する。
3. `command` 内の `${name}` を解析するヘルパーを実装する。
4. 実行時入力を検証するヘルパーを実装する。
5. プレースホルダを実行用 argv に展開するヘルパーを実装する。
6. ツール登録処理を、入力ありツールに対応できる形へ変更する。
7. 既存の実行・progress・ログ出力処理に展開後 argv を接続する。
8. 引数なしツールの後方互換テストを追加する。
9. 引数ありツールの設定読込・入力検証・展開・実行テストを追加する。
10. 必要に応じて `sample.yml` と README の設定例を更新する。
