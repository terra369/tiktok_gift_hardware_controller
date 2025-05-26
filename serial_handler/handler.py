import serial
import serial.tools.list_ports
import threading
import time
import asyncio
import logging

logger = logging.getLogger(__name__)


class SerialGiftProcessor:
    def __init__(
        self,
        port: str,
        baud_rate: int,
        ready_signal_expected: str,
        gift_command_to_send: str,
        gift_queue: asyncio.Queue,
        process_cooldown: float,
    ):
        """
        シリアル通信を介してギフト処理コマンドを送信するクラス。
        ブロッキングI/Oを扱うため、内部でスレッドを使用します。

        :param port: Arduinoが接続されているCOMポート。
        :param baud_rate: ボーレート。
        :param ready_signal_expected: Arduinoからの準備完了信号。
        :param gift_command_to_send: Arduinoへ送信するギフト処理コマンド。
        :param gift_queue: 処理対象のギフト情報を格納するasyncio.Queue。
        :param process_cooldown: ギフト処理後のクールダウン時間（秒）。
        """
        self.port = port
        self.baud_rate = baud_rate
        self.ready_signal_expected = ready_signal_expected.strip()
        self.gift_command_to_send = gift_command_to_send
        self.gift_queue = gift_queue
        self.process_cooldown = process_cooldown

        self.serial_conn = None
        self._stop_event = threading.Event()
        self._processing_thread = None
        self._last_processed_time = 0

    def _initialize_serial(self) -> bool:
        try:
            logger.info(
                f"シリアルポート {self.port} (ボーレート: {self.baud_rate}) に接続試行中..."
            )
            self.serial_conn = serial.Serial(self.port, self.baud_rate, timeout=1)
            time.sleep(2)
            logger.info(f"シリアルポート {self.port} に接続成功しました。")
            return True
        except serial.SerialException as e:
            logger.error(
                f"シリアルポート {self.port} への接続に失敗しました: {e}", exc_info=True
            )
            self._list_available_ports()
            self.serial_conn = None
            return False
        except Exception as e:
            logger.error(f"シリアルポート初期化中に予期せぬエラー: {e}", exc_info=True)
            self.serial_conn = None
            return False

    def _list_available_ports(self):
        ports = serial.tools.list_ports.comports()
        if ports:
            logger.info("利用可能なCOMポート:")
            for port_info in ports:
                logger.info(f"  {port_info.device} - {port_info.description}")
        else:
            logger.info("利用可能なCOMポートが見つかりません。")

    def _process_gifts_loop(self):
        logger.info("ギフト処理スレッドを開始します。")
        if not self._initialize_serial():
            logger.error(
                "シリアルポートの初期化に失敗したため、ギフト処理スレッドを終了します。"
            )
            return

        while not self._stop_event.is_set():
            try:
                current_time = time.time()
                can_process_gift = not self.gift_queue.empty() and (
                    current_time - self._last_processed_time >= self.process_cooldown
                )

                if self.serial_conn and self.serial_conn.is_open and can_process_gift:
                    if self.serial_conn.in_waiting > 0:
                        line = (
                            self.serial_conn.readline()
                            .decode("utf-8", errors="ignore")
                            .strip()
                        )
                        logger.debug(f"シリアル受信: '{line}'")
                        if line == self.ready_signal_expected:
                            logger.info(
                                f"Arduinoから '{self.ready_signal_expected}' 信号を受信しました。"
                                f"クールダウン残り: {max(0, self.process_cooldown - (current_time - self._last_processed_time)):.1f}秒"
                            )
                            gift_info = self.gift_queue.get_nowait()
                            logger.info(f"キューからギフトを取得: {gift_info}")

                            command_to_send = (self.gift_command_to_send + "\n").encode("utf-8")
                            self.serial_conn.write(command_to_send)
                            self.serial_conn.flush()

                            self._last_processed_time = time.time()
                            self.gift_queue.task_done()
                            logger.info(
                                f"ギフトコマンド '{self.gift_command_to_send}' をArduinoに送信しました。ギフト: {gift_info['name']}"
                            )
                        elif line:
                            logger.warning(
                                f"Arduinoから予期しない信号を受信: '{line}' (期待値: '{self.ready_signal_expected}')"
                            )
                    else:
                        pass
                elif not self.serial_conn or not self.serial_conn.is_open:
                    logger.warning("シリアル接続がありません。再接続を試みます...")
                    if self._reconnect_serial():
                        logger.info("シリアル再接続に成功しました。")
                    else:
                        logger.warning("シリアル再接続に失敗。5秒待機します。")
                        self._stop_event.wait(5)

                time.sleep(0.1)

            except serial.SerialTimeoutException:
                logger.debug("シリアル読み取りタイムアウト")
            except serial.SerialException as e:
                logger.error(f"シリアル通信エラー: {e}", exc_info=True)
                logger.info("シリアル再接続を試みます...")
                if self._reconnect_serial():
                    logger.info("シリアル再接続に成功しました。")
                else:
                    logger.error(
                        "シリアル再接続に失敗。スレッドを終了する可能性があります。5秒待機します。"
                    )
                    self._stop_event.wait(5)
            except asyncio.QueueEmpty:
                pass
            except Exception as e:
                logger.error(f"ギフト処理ループで予期せぬエラー: {e}", exc_info=True)
                self._stop_event.wait(5)

        if self.serial_conn and self.serial_conn.is_open:
            logger.info("シリアルポートをクローズします。")
            self.serial_conn.close()
        logger.info("ギフト処理スレッドを終了しました。")

    def _reconnect_serial(self) -> bool:
        logger.info("シリアル再接続処理を開始します。")
        if self.serial_conn and self.serial_conn.is_open:
            try:
                self.serial_conn.close()
                logger.info("既存のシリアル接続をクローズしました。")
            except Exception as e:
                logger.error(
                    f"既存のシリアル接続クローズ中にエラー: {e}", exc_info=True
                )

        time.sleep(3)
        return self._initialize_serial()

    def start_processing(self):
        if self._processing_thread and self._processing_thread.is_alive():
            logger.warning("ギフト処理スレッドは既に実行中です。")
            return

        self._stop_event.clear()
        self._processing_thread = threading.Thread(
            target=self._process_gifts_loop, daemon=True
        )
        self._processing_thread.start()
        logger.info("ギフト処理スレッドの開始を要求しました。")

    def stop_processing(self):
        logger.info("ギフト処理スレッドの停止を要求します...")
        self._stop_event.set()
        if self._processing_thread and self._processing_thread.is_alive():
            self._processing_thread.join(timeout=5)
            if self._processing_thread.is_alive():
                logger.warning("ギフト処理スレッドの終了待機がタイムアウトしました。")
            else:
                logger.info("ギフト処理スレッドは正常に終了しました。")
        else:
            logger.info(
                "ギフト処理スレッドは実行されていなかったか、既に終了しています。"
            )
