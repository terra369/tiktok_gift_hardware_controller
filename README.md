# TikTokギフト連動ハードウェアコントローラー

## 概要

このプロジェクトは、指定したTikTokアカウントのライブストリームを監視し、特定のギフトが送信された際に、接続されたArduinoなどのハードウェアデバイスへシリアルコマンドを送信するPythonアプリケーションです。

## 主な機能

- 指定したTikTokユーザーのライブ配信をリアルタイムで監視
- 設定ファイルで定義された特定のギフト名を検知
- ギフト検知時に、シリアルポート経由で接続されたハードウェア（例: Arduino）にコマンドを送信
- 詳細なロギング機能 (コンソールおよびファイル出力)
- 設定ファイル (`config/settings.ini`) による柔軟なカスタマイズ
- 非同期処理 (`asyncio`) の活用による効率的なリソース管理
- 接続エラー時の自動再試行ロジック
- グレースフルシャットダウン対応

## ディレクトリ構成

```
tiktok_gift_hardware_controller/
├── main_controller.py        # メイン処理、エントリーポイント
├── serial_handler/           # シリアル通信処理モジュール
│   ├── __init__.py
│   └── handler.py            # SerialGiftProcessorクラス
├── config/                   # 設定ファイルディレクトリ
│   └── settings.ini          # アプリケーション設定ファイル
├── requirements.txt          # 依存ライブラリリスト
├── requirements.in           # 依存ライブラリリスト
├── .gitignore                # Git管理対象外ファイル定義
└── README.md                 # このファイル
```

## 要件

- Python 3.8 以上
- 必要なライブラリ (詳細は `requirements.txt` を参照)
  - `pyserial`: シリアル通信用
  - `TikTokLive`: TikTokライブ連携用 (特定のフォークやバージョンが必要な場合があります)

## セットアップとインストール

1. **リポジトリのクローン (またはファイルのダウンロード):**

    ```bash
    git clone https://github.com/your_username/tiktok_gift_hardware_controller.git # もしリポジトリがあれば
    cd tiktok_gift_hardware_controller
    ```

2. **Python仮想環境の作成と有効化 (推奨):**

    ```bash
    python -m venv .venv
    # Windows
    .venv\Scripts\activate
    # macOS/Linux
    source .venv/bin/activate
    ```

3. **依存ライブラリのインストール:**
    `requirements.txt` を確認し、特に `TikTokLive` の行を適切なバージョンやソースに修正してから実行してください。

    ```bash
    pip install -r requirements.txt
    ```

    **注意:** `TikTokLive` ライブラリは、動作するバージョンを見つけるか、特定のフォークを指定する必要がある場合があります。公式リポジトリや関連情報を確認してください。

## 設定

アプリケーションの動作は `config/settings.ini` ファイルで設定します。
実行前に、このファイルを実際の環境に合わせて編集してください。

```ini
[TikTok]
# 監視対象のTikTokユーザー名
USERNAME = nakamura.a.k.a.hippy
# 検知対象のギフト名 (正確な名称。TikTokLiveライブラリでの取得名に合わせる)
TARGET_GIFT_NAME = Swan

[Serial]
# Arduinoが接続されているCOMポート (例: Windowsでは COM3, macOS/Linuxでは /dev/ttyUSB0 や /dev/tty.usbmodemXXXXX)
PORT = COM3
# ボーレート (Arduinoのスケッチと合わせる)
BAUD_RATE = 9600
# Arduinoからの "Ready" 信号 (末尾の改行は含めない想定で、Python側でstrip()する)
READY_SIGNAL = Ready
# Arduinoへ送信するギフト処理コマンド (末尾の改行はPython側で付与する)
GIFT_COMMAND = gift

[Application]
# 1つのギフトを処理した後、次のギフトを処理するまでの最小クールダウン時間（秒）
GIFT_PROCESS_COOLDOWN = 22
# TikTok接続試行時のリトライ間隔（秒）
TIKTOK_RECONNECT_DELAY = 5
# ギフトキューの最大サイズ (0は無限)
MAX_GIFT_QUEUE_SIZE = 0
# ログレベル (DEBUG, INFO, WARNING, ERROR, CRITICAL)
LOG_LEVEL = INFO
# ログファイルパス (空の場合はコンソール出力のみ)
LOG_FILE_PATH = ./logs/app.log

# (オプション) TikTokLiveClientに追加で渡すパラメータがある場合
# [TikTokClientOptions]
# signer_url = http://localhost:8080/sign
# # その他のTikTokLiveClientが受け付けるオプション...
```

必要に応じて以下の環境変数を設定することで、Cookie や署名サーバーの情報を
`TikTokGiftDetector` に渡すことができます。

```bash
TIKTOK_COOKIES='{"sessionid":"..."}'
TIKTOK_SIGNER_URL=http://localhost:8080/sign
TIKTOK_SIGN_API_KEY=<your-sign-api-key>
```

**主な設定項目:**

- `USERNAME`: 監視したいTikTokユーザーのアカウント名 (例: `@example_user`)。
- `TARGET_GIFT_NAME`: 検知したいギフトの名前。TikTok上で表示される正確な名前を指定してください。
- `TARGET_GIFT_ID`: (オプション) ギフト名よりも安定してギフトを特定できる場合、ギフトIDを指定します。どちらかが設定されていれば動作します。ID指定を推奨。
- `PORT`: Arduinoなどが接続されているシリアルポート。
- `BAUD_RATE`: シリアル通信のボーレート。Arduino側の設定と一致させてください。
- `READY_SIGNAL`: Arduino側から送信される準備完了を示す文字列。
- `GIFT_COMMAND`: Arduino側に送信する、ギフト処理を指示するコマンド文字列。
- `LOG_LEVEL`: 出力するログのレベル (`DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`)。
- `LOG_FILE_PATH`: ログを保存するファイルパス。空にするとファイル出力は行われません。
- `[TikTokClientOptions]`: (オプション) `TikTokLive` ライブラリのクライアント初期化時に追加のパラメータが必要な場合（例: 署名サーバーのURLなど）に設定します。

## 実行方法

1. `config/settings.ini` を上記に従って設定します。
2. 対象のTikTokアカウントがライブ配信中であることを確認します。
3. ターミナルでプロジェクトのルートディレクトリに移動し、以下のコマンドを実行します。

    ```bash
    python main_controller.py
    ```

アプリケーションはログをコンソールに出力し、設定されていればログファイルにも書き込みます。
Ctrl+C で安全にシャットダウンできます。

## ロギング

- ログはコンソールと、`config/settings.ini` の `LOG_FILE_PATH` で指定されたファイルに出力されます。
- ログレベルは `LOG_LEVEL` で制御できます。
  - `INFO`: 通常の動作状況、主要なイベントのログ。
  - `DEBUG`: より詳細な情報、問題調査時に有用。

## トラブルシューティング (簡易)

- **TikTokに接続できない:**
  - `USERNAME` が正しいか確認してください。
  - `TikTokLive` ライブラリのバージョンや設定が適切か確認してください。特に署名サービスなどの追加設定が必要な場合があります (`[TikTokClientOptions]` セクション)。
  - ネットワーク接続を確認してください。
- **ギフトが検知されない:**
  - `TARGET_GIFT_NAME` がTikTok上で表示される名前と完全に一致しているか確認してください。
  - 可能であれば `TARGET_GIFT_ID` での指定を試みてください。IDは `LOG_LEVEL=DEBUG` にしてギフト受信時のログ (`event.gift.id`) から確認できます。
  - 対象アカウントがライブ配信中か確認してください。
- **シリアル通信がうまくいかない:**
  - `PORT` の指定が正しいか確認してください。
  - `BAUD_RATE` がArduino側の設定と一致しているか確認してください。
  - ArduinoがPCに正しく接続されているか、ドライバがインストールされているか確認してください。
  - `READY_SIGNAL` と `GIFT_COMMAND` がArduino側のスケッチと整合性が取れているか確認してください。

## 今後の改善点 (例)

- GUIの追加による操作性の向上
- 対応ハードウェアの拡充
- ギフトの種類に応じた複数のコマンド送信機能
- より詳細なエラーハンドリングとユーザーへのフィードバック
