import asyncio
import configparser
import logging
import signal
import os
import sys
from pathlib import Path

from serial_handler.handler import SerialGiftProcessor
from TikTokLive import TikTokLiveClient
from TikTokLive.events import ConnectEvent, GiftEvent, DisconnectEvent
from TikTokLive.client.errors import (
    UserOfflineError,
    AlreadyConnectedError,
)

logger = logging.getLogger()
shutdown_event = asyncio.Event()
_connected_event = asyncio.Event()

_serial_processor_ref = None


def setup_logging(log_level_str: str, log_file_path_str: str):
    numeric_level = getattr(logging, log_level_str.upper(), None)
    if not isinstance(numeric_level, int):
        logging.warning(f"無効なログレベル: {log_level_str}。INFOレベルを使用します。")
        numeric_level = logging.INFO

    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    if log_file_path_str:
        try:
            log_file_path = Path(log_file_path_str)
            log_file_path.parent.mkdir(parents=True, exist_ok=True)
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
    config = configparser.ConfigParser()
    if not Path(config_path).exists():
        logger.error(f"設定ファイルが見つかりません: {config_path}")
        raise FileNotFoundError(f"設定ファイルが見つかりません: {config_path}")

    try:
        config.read(config_path, encoding="utf-8")
        required_sections = {
            "TikTok": ["USERNAME"],
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
    config = None
    global _serial_processor_ref
    tiktok_client = None
    connection_task = None

    try:
        base_dir = Path(__file__).resolve().parent
        config_file = base_dir / "config" / "settings.ini"

        config = load_config(str(config_file))

        log_level = config.get("Application", "LOG_LEVEL", fallback="INFO")
        log_file_path = config.get("Application", "LOG_FILE_PATH", fallback=None)
        setup_logging(log_level, log_file_path)

        logger.info("アプリケーションを開始します...")

        max_queue_size = config.getint("Application", "MAX_GIFT_QUEUE_SIZE", fallback=0)
        gift_queue = asyncio.Queue(maxsize=max_queue_size)
        logger.info(
            f"ギフトキューを初期化しました (最大サイズ: {max_queue_size if max_queue_size > 0 else '無限'})。"
        )

        device_mode = config.get("Serial", "DEVICE_MODE", fallback="WAIT_FOR_DEVICE")
        port = config.get("Serial", "PORT")

        tiktok_username = config.get("TikTok", "USERNAME")
        FETCH_GIFT_INFO = True
        RECONNECT_DELAY = config.getint(
            "Application", "TIKTOK_RECONNECT_DELAY", fallback=60
        )

        tiktok_client = TikTokLiveClient(unique_id=f"@{tiktok_username}")

        serial_port = config.get("Serial", "PORT", fallback=None)
        baud_rate = config.getint("Serial", "BAUD_RATE", fallback=9600)
        serial_processor = None

        if serial_port:
            try:
                ready_signal = config.get("Serial", "READY_SIGNAL")
                gift_command = config.get("Serial", "GIFT_COMMAND")
                process_cooldown = config.getfloat(
                    "Application", "GIFT_PROCESS_COOLDOWN", fallback=22.0
                )

                serial_processor = SerialGiftProcessor(
                    port=serial_port,
                    baud_rate=baud_rate,
                    ready_signal=ready_signal,
                    gift_command=gift_command,
                    gift_queue=gift_queue,
                    process_cooldown=process_cooldown,
                )
                serial_processor.start_processing()
                _serial_processor_ref = serial_processor
                logger.info(
                    f"シリアルプロセッサを開始しました。ポート: {serial_port}, コマンド: '{gift_command}', ready信号: '{ready_signal}'"
                )
            except Exception as e:
                logger.error(
                    f"シリアルプロセッサの初期化または開始に失敗: {serial_port}, {e}",
                    exc_info=True,
                )
                serial_processor = None
                _serial_processor_ref = None
        else:
            logger.warning(
                "シリアルポートが設定されていません。シリアル通信は無効です。"
            )

        @tiktok_client.on(ConnectEvent)
        async def on_connect(_: ConnectEvent):
            logger.info(
                f"{tiktok_username} に接続しました！ Room ID: {tiktok_client.room_id}"
            )
            _connected_event.set()

        @tiktok_client.on(GiftEvent)
        async def on_gift(event: GiftEvent):
            gift_name = (
                event.gift.name if event.gift and event.gift.name else "不明なギフト"
            )
            sender_name = (
                event.user.nickname
                if event.user and event.user.nickname
                else event.user.unique_id if event.user else "不明な送信者"
            )

            logger.info(
                f"ギフト受信: {sender_name} さんから「{gift_name}」x{event.repeat_count}"
            )

            if gift_name == "You're awesome":
                logger.info(
                    f"「You're awesome」ギフトを検出しました。送信者: {sender_name}, 現在のコンボ数: {event.repeat_count}"
                )
                if _serial_processor_ref:
                    try:
                        logger.info(
                            f"シリアル処理のため、「You're awesome」ギフト (コンボ数: {event.repeat_count}) を1個キューに追加します。"
                        )
                        await _serial_processor_ref.add_gift_item(gift_name)
                        logger.info(
                            f"「You're awesome」ギフト (コンボ数: {event.repeat_count}) のキュー追加が完了しました。"
                        )
                    except Exception as e:
                        logger.error(
                            f"「You're awesome」ギフトの処理キュー追加中にエラー: {e}",
                            exc_info=True,
                        )
                else:
                    logger.info(
                        "シリアルプロセッサが無効なため、「You're awesome」ギフトのキュー追加はスキップされました。"
                    )

        @tiktok_client.on(DisconnectEvent)
        async def on_disconnect(_: DisconnectEvent):
            logger.info(f"{tiktok_username} との接続が切断されました。")
            _connected_event.clear()

        while not shutdown_event.is_set():
            try:
                _connected_event.clear()
                logger.info(
                    f"{tiktok_username} のTikTok Live監視を開始します (fetch_gift_info={FETCH_GIFT_INFO})..."
                )

                is_live = await tiktok_client.web.fetch_is_live(
                    unique_id=tiktok_username
                )
                if not is_live:
                    logger.warning(
                        f"{tiktok_username} は現在オフラインです。{RECONNECT_DELAY}秒後に再試行します。"
                    )
                    await asyncio.sleep(RECONNECT_DELAY)
                    continue

                connection_task = await tiktok_client.start(
                    fetch_gift_info=FETCH_GIFT_INFO
                )

                try:
                    await asyncio.wait_for(_connected_event.wait(), timeout=15.0)
                    logger.info(
                        f"{tiktok_username} への接続が確認されました。ルームID: {tiktok_client.room_id}"
                    )
                except asyncio.TimeoutError:
                    logger.error(
                        f"{tiktok_username} への接続が15秒以内に確認できませんでした。切断して再試行します。"
                    )
                    if tiktok_client.connected:
                        await tiktok_client.disconnect()
                    if connection_task and not connection_task.done():
                        connection_task.cancel()
                        try:
                            await connection_task
                        except asyncio.CancelledError:
                            logger.info(
                                "接続試行タスクはタイムアウトによりキャンセルされました。"
                            )
                    await asyncio.sleep(RECONNECT_DELAY)
                    continue

                await connection_task
                logger.warning(
                    f"{tiktok_username} のTikTok Live監視が例外なしで終了しました。{RECONNECT_DELAY}秒後に再接続します。"
                )

            except UserOfflineError:
                logger.info(
                    f"{tiktok_username} はオフラインか、配信を終了しました。{RECONNECT_DELAY}秒後に再試行します。"
                )
            except AlreadyConnectedError:
                logger.warning(
                    f"{tiktok_username} には既に接続済みです。現在の接続を使用します。"
                )
                if tiktok_client.connected:
                    await tiktok_client.disconnect()
                if connection_task and not connection_task.done():
                    connection_task.cancel()
                _connected_event.clear()
            except ConnectionRefusedError:
                logger.error(
                    f"{tiktok_username} への接続が拒否されました。{RECONNECT_DELAY}秒後に再試行します。"
                )
            except asyncio.CancelledError:
                logger.info(
                    "メインの監視ループがキャンセルされました。シャットダウンします。"
                )
                break
            except Exception as e:
                logger.error(
                    f"TikTok Live監視中に予期せぬエラーが発生しました: {e}",
                    exc_info=True,
                )
            finally:
                if tiktok_client and tiktok_client.connected:
                    logger.info(f"{tiktok_username} との接続を切断しています...")
                    await tiktok_client.disconnect()
                    logger.info(f"{tiktok_username} との接続を正常に切断しました。")
                if connection_task and not connection_task.done():
                    connection_task.cancel()
                    try:
                        await connection_task
                    except asyncio.CancelledError:
                        logger.info(
                            "監視タスクのクリーンアップ中にキャンセルを処理しました。"
                        )
                    except Exception as e_task_cleanup:
                        logger.error(
                            f"監視タスクのクリーンアップ中にエラー: {e_task_cleanup}"
                        )
                connection_task = None
                _connected_event.clear()

                if not shutdown_event.is_set():
                    logger.info(f"{RECONNECT_DELAY}秒後に再接続を試みます...")
                    try:
                        await asyncio.wait_for(
                            shutdown_event.wait(), timeout=RECONNECT_DELAY
                        )
                        if shutdown_event.is_set():
                            logger.info(
                                "シャットダウンが要求されたため、再接続を中止します。"
                            )
                            break
                    except asyncio.TimeoutError:
                        pass

        logger.info("TikTok監視ループを終了しました。")

    except FileNotFoundError as e:
        logger.critical(f"起動エラー: {e}")
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
        if connection_task and not connection_task.done():
            logger.info("メインのTikTok接続タスクをキャンセルします...")
            connection_task.cancel()
            try:
                await connection_task
            except asyncio.CancelledError:
                logger.info("メインのTikTok接続タスクは正常にキャンセルされました。")
            except Exception as e:
                logger.error(
                    f"メインのTikTok接続タスク停止中にエラー: {e}", exc_info=True
                )

        if tiktok_client and tiktok_client.connected:
            logger.info("シャットダウン時にTikTokクライアントを切断します...")
            await tiktok_client.stop()
            logger.info("TikTokクライアントを正常に切断しました。")

        if _serial_processor_ref:
            logger.info("シリアルプロセッサを停止しています...")
            _serial_processor_ref.stop()
            logger.info("シリアルプロセッサを停止しました。")

        remaining_tasks = [
            t for t in asyncio.all_tasks() if t is not asyncio.current_task()
        ]
        if remaining_tasks:
            logger.info(
                f"残りのバックグラウンドタスク ({len(remaining_tasks)}) をキャンセルして待機します..."
            )
            for task in remaining_tasks:
                task.cancel()
            await asyncio.gather(*remaining_tasks, return_exceptions=True)
            logger.info("残りのタスクの処理が完了しました。")

        logger.info("アプリケーションは正常にシャットダウンしました。")


def signal_handler(sig, frame):
    logger.info(f"{signal.Signals(sig).name} を受信。シャットダウンを開始します...")
    global _serial_processor_ref
    if _serial_processor_ref:
        logger.info("シグナルハンドラ: シリアルプロセッサを停止しています...")
        _serial_processor_ref.stop()
        logger.info("シグナルハンドラ: シリアルプロセッサを停止しました。")
    shutdown_event.set()


if __name__ == "__main__":

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
