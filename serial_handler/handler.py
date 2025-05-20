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
        ready_signal: str,
        gift_command: str,
        gift_queue: asyncio.Queue,
        process_cooldown: float,
    ):
        """
        シリアル通信を介してギフト処理コマンドを送信するクラス。
        ブロッキングI/Oを扱うため、内部でスレッドを使用します。

        :param port: Arduinoが接続されているCOMポート。
        :param baud_rate: ボーレート。
        :param ready_signal: Arduinoからの準備完了信号。
        :param gift_command: Arduinoへ送信するギフト処理コマンド。
        :param gift_queue: 処理対象のギフト情報を格納するasyncio.Queue。
        :param process_cooldown: ギフト処理後のクールダウン時間（秒）。
        """
        self.port = port
        self.baud_rate = baud_rate
        self.ready_signal = ready_signal.strip()  # 末尾改行を削除
        self.gift_command = gift_command
        self.gift_queue = gift_queue
        self.process_cooldown = process_cooldown

        self.serial_conn = None
        self._stop_event = threading.Event()
        self._processing_thread = None
        self._last_processed_time = 0

    def _initialize_serial(self) -> bool:
        """シリアルポートを初期化して接続します。"""
        try:
            logger.info(
                f"シリアルポート {self.port} (ボーレート: {self.baud_rate}) に接続試行中..."
            )
            self.serial_conn = serial.Serial(self.port, self.baud_rate, timeout=1)
            time.sleep(2)  # Arduinoのリセット直後の安定化のため
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
        """利用可能なCOMポートをリスト表示します。"""
        ports = serial.tools.list_ports.comports()
        if ports:
            logger.info("利用可能なCOMポート:")
            for port_info in ports:
                logger.info(f"  {port_info.device} - {port_info.description}")
        else:
            logger.info("利用可能なCOMポートが見つかりません。")

    def _process_gifts_loop(self):
        """ギフト処理のメインループ（スレッドで実行）。"""
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
                    # ArduinoからのReady信号を待つ
                    if self.serial_conn.in_waiting > 0:
                        line = (
                            self.serial_conn.readline()
                            .decode("utf-8", errors="ignore")
                            .strip()
                        )
                        logger.debug(f"シリアル受信: '{line}'")
                        if line == self.ready_signal:
                            logger.info(
                                f"Arduinoから '{self.ready_signal}' 信号を受信しました。"
                                f"クールダウン残り: {max(0, self.process_cooldown - (current_time - self._last_processed_time)):.1f}秒"
                            )
                            gift_info = self.gift_queue.get_nowait()
                            logger.info(f"キューからギフトを取得: {gift_info}")

                            command_to_send = (self.gift_command + "\n").encode("utf-8")
                            self.serial_conn.write(command_to_send)
                            self.serial_conn.flush()  # 送信バッファをフラッシュ

                            self._last_processed_time = time.time()
                            self.gift_queue.task_done()
                            logger.info(
                                f"ギフトコマンド '{self.gift_command}' をArduinoに送信しました。ギフト: {gift_info['name']}"
                            )
                        elif line:  # Ready信号ではないが何か受信した場合
                            logger.warning(
                                f"Arduinoから予期しない信号を受信: '{line}' (期待値: '{self.ready_signal}')"
                            )
                    else:  # in_waiting == 0
                        # Ready信号を待っている間にCPUを過度に消費しないように
                        # ただし、シリアルタイムアウト(timeout=1)があるので、readline()がブロックしすぎることはない
                        pass
                elif not self.serial_conn or not self.serial_conn.is_open:
                    logger.warning("シリアル接続がありません。再接続を試みます...")
                    if self._reconnect_serial():
                        logger.info("シリアル再接続に成功しました。")
                    else:
                        logger.warning("シリアル再接続に失敗。5秒待機します。")
                        self._stop_event.wait(5)  # 停止イベントを待ちつつスリープ

                # ループのCPU負荷軽減と応答性のバランス
                # 頻繁なポーリングが必要な場合、このsleepは短くするか、より高度なイベントドリブンな方法を検討
                time.sleep(0.1)

            except serial.SerialTimeoutException:
                logger.debug(
                    "シリアル読み取りタイムアウト"
                )  # 通常動作の一部なのでdebugレベル
            except serial.SerialException as e:
                logger.error(f"シリアル通信エラー: {e}", exc_info=True)
                logger.info("シリアル再接続を試みます...")
                if self._reconnect_serial():
                    logger.info("シリアル再接続に成功しました。")
                else:
                    logger.error(
                        "シリアル再接続に失敗。スレッドを終了する可能性があります。5秒待機します。"
                    )
                    # 深刻なエラーの場合、スレッドを終了させるか、より堅牢なリトライループが必要
                    self._stop_event.wait(5)
            except (
                asyncio.QueueEmpty
            ):  # get_nowaitで発生しうるが、can_process_giftでチェック済み
                pass  # キューが空なのは通常状態
            except Exception as e:
                logger.error(f"ギフト処理ループで予期せぬエラー: {e}", exc_info=True)
                # 予期せぬエラーの場合、クールダウンして再試行
                self._stop_event.wait(5)

        # ループ終了時の処理
        if self.serial_conn and self.serial_conn.is_open:
            logger.info("シリアルポートをクローズします。")
            self.serial_conn.close()
        logger.info("ギフト処理スレッドを終了しました。")

    def _reconnect_serial(self) -> bool:
        """シリアル接続の再接続を試みます。"""
        logger.info("シリアル再接続処理を開始します。")
        if self.serial_conn and self.serial_conn.is_open:
            try:
                self.serial_conn.close()
                logger.info("既存のシリアル接続をクローズしました。")
            except Exception as e:
                logger.error(
                    f"既存のシリアル接続クローズ中にエラー: {e}", exc_info=True
                )

        # 数秒待機
        time.sleep(3)
        return self._initialize_serial()

    def start_processing(self):
        """ギフト処理スレッドを開始します。"""
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
        """ギフト処理スレッドを停止します。"""
        logger.info("ギフト処理スレッドの停止を要求します...")
        self._stop_event.set()
        if self._processing_thread and self._processing_thread.is_alive():
            self._processing_thread.join(timeout=5)  # タイムアウト付きで待機
            if self._processing_thread.is_alive():
                logger.warning("ギフト処理スレッドの終了待機がタイムアウトしました。")
            else:
                logger.info("ギフト処理スレッドは正常に終了しました。")
        else:
            logger.info(
                "ギフト処理スレッドは実行されていなかったか、既に終了しています。"
            )
