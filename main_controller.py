import asyncio
import configparser
import logging
import signal
import os
import sys
from pathlib import Path

from tiktok_detector.detector import TikTokGiftDetector
from serial_handler.handler import SerialGiftProcessor

# ルートロガーの設定
logger = logging.getLogger()

# グレースフルシャットダウンのためのイベント
shutdown_event = asyncio.Event()


def setup_logging(log_level_str: str, log_file_path_str: str):
    """ロギングを設定します。"""
    numeric_level = getattr(logging, log_level_str.upper(), None)
    if not isinstance(numeric_level, int):
        logging.warning(f"無効なログレベル: {log_level_str}。INFOレベルを使用します。")
        numeric_level = logging.INFO

    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )

    # コンソールハンドラ
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # ファイルハンドラ (パスが指定されている場合)
    if log_file_path_str:
        try:
            log_file_path = Path(log_file_path_str)
            log_file_path.parent.mkdir(
                parents=True, exist_ok=True
            )  # 必要ならディレクトリ作成
            file_handler = logging.FileHandler(log_file_path, encoding="utf-8")
            file_handler.setFormatter(formatter)
            logger.addHandler(file_handler)
            logger.info(f"ログをファイルに出力します: {log_file_path.resolve()}")
        except Exception as e:
            logging.error(
                f"ログファイルハンドラの設定に失敗しました: {e}", exc_info=True
            )
            logging.warning("ログはコンソールのみに出力されます。")

    logger.setLevel(numeric_level)
    logger.info(f"ロギングレベルを {log_level_str.upper()} に設定しました。")


def load_config(config_path: str) -> configparser.ConfigParser:
    """設定ファイルを読み込みます。"""
    config = configparser.ConfigParser()
    if not Path(config_path).exists():
        logger.error(f"設定ファイルが見つかりません: {config_path}")
        raise FileNotFoundError(f"設定ファイルが見つかりません: {config_path}")

    try:
        config.read(config_path, encoding="utf-8")
        # 必須セクションとキーの存在チェック (例)
        required_sections = {
            "TikTok": ["USERNAME", "TARGET_GIFT_NAME"],
            "Serial": ["PORT", "BAUD_RATE", "READY_SIGNAL", "GIFT_COMMAND"],
            "Application": [
                "GIFT_PROCESS_COOLDOWN",
                "TIKTOK_RECONNECT_DELAY",
                "MAX_GIFT_QUEUE_SIZE",
                "LOG_LEVEL",
            ],
        }
        for section, keys in required_sections.items():
            if not config.has_section(section):
                raise ValueError(
                    f"設定ファイルに必須セクション '{section}' がありません。"
                )
            for key in keys:
                if not config.has_option(section, key):
                    # TARGET_GIFT_ID はオプションなのでチェックから除外
                    if section == "TikTok" and key == "TARGET_GIFT_ID":
                        continue
                    raise ValueError(
                        f"設定ファイルセクション '{section}' に必須キー '{key}' がありません。"
                    )
        logger.info(f"設定ファイルを読み込みました: {config_path}")
        return config
    except configparser.Error as e:
        logger.error(f"設定ファイルの解析エラー: {e}", exc_info=True)
        raise
    except ValueError as e:
        logger.error(f"設定ファイルの内容エラー: {e}", exc_info=True)
        raise


async def main():
    """アプリケーションのメイン処理。"""
    config = None
    serial_processor = None
    tiktok_detector_task = None

    try:
        # 設定ファイルのパス (スクリプトの場所基準で config/settings.ini)
        base_dir = Path(__file__).resolve().parent
        config_file = base_dir / "config" / "settings.ini"

        config = load_config(str(config_file))

        # ロギング設定
        log_level = config.get("Application", "LOG_LEVEL", fallback="INFO")
        log_file_path = config.get("Application", "LOG_FILE_PATH", fallback=None)
        setup_logging(log_level, log_file_path)

        logger.info("アプリケーションを開始します...")

        # asyncioキューの初期化
        max_queue_size = config.getint("Application", "MAX_GIFT_QUEUE_SIZE", fallback=0)
        gift_queue = asyncio.Queue(maxsize=max_queue_size)
        logger.info(
            f"ギフトキューを初期化しました (最大サイズ: {max_queue_size if max_queue_size > 0 else '無限'})。"
        )

        # SerialGiftProcessorの初期化と開始
        serial_processor = SerialGiftProcessor(
            port=config.get("Serial", "PORT"),
            baud_rate=config.getint("Serial", "BAUD_RATE"),
            ready_signal=config.get("Serial", "READY_SIGNAL"),
            gift_command=config.get("Serial", "GIFT_COMMAND"),
            gift_queue=gift_queue,
            process_cooldown=config.getfloat("Application", "GIFT_PROCESS_COOLDOWN"),
        )
        serial_processor.start_processing()

        # TikTokGiftDetectorの初期化と非同期タスクとしての開始
        # TikTokLiveClientに追加オプションが必要な場合は、settings.iniにセクションを追加し、ここで読み込む
        # 例: client_options = {"signer_url": config.get("TikTokSigner", "URL", fallback=None)}
        # 現状は追加オプションなしで初期化
        client_options = {}  # 必要に応じて設定ファイルから読み込む
        if config.has_section("TikTokClientOptions"):
            client_options = dict(config.items("TikTokClientOptions"))
            logger.info(f"TikTokLiveClientに追加オプションを渡します: {client_options}")

        detector = TikTokGiftDetector(
            username=config.get("TikTok", "USERNAME"),
            target_gift_name=config.get("TikTok", "TARGET_GIFT_NAME"),
            target_gift_id=config.get(
                "TikTok", "TARGET_GIFT_ID", fallback=None
            ),  # fallbackでNoneを許容
            gift_queue=gift_queue,
            reconnect_delay=config.getint("Application", "TIKTOK_RECONNECT_DELAY"),
            client_options=client_options,
        )
        tiktok_detector_task = asyncio.create_task(detector.run())
        logger.info("TikTokギフト検知タスクを開始しました。")

        # シャットダウンシグナルを待機
        logger.info("アプリケーション実行中。Ctrl+C で停止します。")
        await shutdown_event.wait()

    except FileNotFoundError as e:
        logger.critical(f"起動エラー: {e}")
        # setup_loggingが呼ばれる前にエラーが発生する可能性を考慮し、基本的なloggingを使う
        logging.basicConfig(level=logging.ERROR)
        logging.critical(f"起動エラー: {e}")
    except (configparser.Error, ValueError) as e:
        logger.critical(f"設定エラー: {e}")
        logging.basicConfig(level=logging.ERROR)
        logging.critical(f"設定エラー: {e}")
    except Exception as e:
        logger.critical(
            f"予期せぬエラーによりメイン処理が停止しました: {e}", exc_info=True
        )
    finally:
        logger.info("アプリケーションのシャットダウン処理を開始します...")
        if tiktok_detector_task and not tiktok_detector_task.done():
            logger.info("TikTokギフト検知タスクをキャンセルします...")
            tiktok_detector_task.cancel()
            try:
                await tiktok_detector_task
                logger.info("TikTokギフト検知タスクは正常にキャンセルされました。")
            except asyncio.CancelledError:
                logger.info(
                    "TikTokギフト検知タスクがキャンセルされました (asyncio.CancelledError)。"
                )
            except Exception as e:
                logger.error(
                    f"TikTokギフト検知タスクのキャンセル中にエラー: {e}", exc_info=True
                )

        if serial_processor:
            logger.info("シリアルギフトプロセッサを停止します...")
            serial_processor.stop_processing()
            logger.info("シリアルギフトプロセッサの停止処理が完了しました。")

        logger.info("アプリケーションは正常にシャットダウンしました。")


def signal_handler(sig, frame):
    logger.info(
        f"シグナル {sig} を受信しました。グレースフルシャットダウンを開始します..."
    )
    shutdown_event.set()


if __name__ == "__main__":
    # 基本的なロガーを早期に設定 (設定ファイル読み込み前用)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    # シグナルハンドラの設定
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info(
            "KeyboardInterrupt を受信。アプリケーションを終了します。(asyncio.run外)"
        )
    except Exception as e:
        logger.critical(
            f"asyncio.runの実行中に予期せぬエラーが発生しました: {e}", exc_info=True
        )
