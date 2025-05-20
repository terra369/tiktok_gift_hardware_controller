import asyncio
import logging
from TikTokLive import TikTokLiveClient
from TikTokLive.types.events import ConnectEvent, DisconnectEvent, GiftEvent
# 【要確認】TikTokLiveの具体的な例外クラスをインポートする必要があるかもしれません
# from TikTokLive.types.errors import SomeSpecificConnectionError

logger = logging.getLogger(__name__)

class TikTokGiftDetector:
    def __init__(
        self,
        username: str,
        target_gift_name: str,
        target_gift_id: str,
        gift_queue: asyncio.Queue,
        reconnect_delay: int,
        client_options: dict | None = None,
        stop_event: asyncio.Event | None = None,
    ) -> None:
        """
        TikTokライブを監視し、特定のギフトを検知してキューに追加するクラス。

        :param username: 監視対象のTikTokユーザー名。
        :param target_gift_name: 検知対象のギフト名。
        :param target_gift_id: (オプション) 検知対象のギフトID。
        :param gift_queue: 検知したギフト情報を格納するasyncio.Queue。
        :param reconnect_delay: TikTok接続リトライ時の遅延時間（秒）。
        :param client_options: (オプション) TikTokLiveClientに追加で渡す設定。
            署名サーバーの設定などがここに含まれる想定。
        :param stop_event: シャットダウン要求を検知するためのイベント。
        """
        self.username = username
        self.target_gift_name = target_gift_name
        self.target_gift_id = target_gift_id
        self.gift_queue = gift_queue
        self.reconnect_delay = reconnect_delay
        self.stop_event = stop_event

        # TikTokLiveClientの初期化
        # 【最重要課題】署名サービスなどの追加パラメータが必要な場合、client_options経由で渡すか、
        # この部分のロジックを修正・拡張する必要があります。
        # 現状では、TikTokLiveClientの基本的な初期化のみを行っています。
        # 例: client_options = {"signer_url": "http://localhost:8080/sign"} など
        client_init_params = {
            "unique_id": self.username,
            "enable_extended_gift_info": True,
            **(client_options if client_options else {})
        }
        logger.info(f"TikTokLiveClientを初期化します: {client_init_params}")
        try:
            self.client = TikTokLiveClient(**client_init_params)
        except Exception as e:
            logger.error(f"TikTokLiveClientの初期化に失敗しました: {e}", exc_info=True)
            # アプリケーションの起動をここで止めるか、上位でエラーハンドリングするか検討が必要
            raise

        # イベントリスナーの登録
        self.client.add_listener("connect", self.on_connect)
        self.client.add_listener("disconnect", self.on_disconnect)
        self.client.add_listener("gift", self.on_gift)

    async def on_connect(self, event: ConnectEvent):
        logger.info(f"TikTok Liveに接続しました: {self.username}")

    async def on_disconnect(self, event: DisconnectEvent):
        logger.warning(f"TikTok Liveから切断されました: {self.username}. runメソッド内で再接続が試みられます。")

    async def on_gift(self, event: GiftEvent):
        logger.debug(f"ギフト受信: {event.gift.name} (ID: {event.gift.id}) from {event.user.nickname}")

        is_target_gift = False
        if self.target_gift_id:
            if str(event.gift.id) == self.target_gift_id:
                is_target_gift = True
        elif event.gift.name == self.target_gift_name:
            is_target_gift = True

        if is_target_gift:
            gift_info = {
                "name": event.gift.name,
                "id": event.gift.id,
                "user": event.user.nickname,
                "timestamp": asyncio.get_event_loop().time() # UNIXタイムスタンプ
            }
            try:
                self.gift_queue.put_nowait(gift_info)
                logger.info(f"ターゲットギフト検知: {gift_info['name']} (ID: {gift_info['id']}) をキューに追加しました。送信者: {gift_info['user']}")
            except asyncio.QueueFull:
                logger.warning(f"ギフトキューが満杯です。ギフト {gift_info['name']} は破棄されました。")
            except Exception as e:
                logger.error(f"ギフトキューへの追加中にエラーが発生しました: {e}", exc_info=True)

    async def run(self):
        """TikTokライブの監視を開始・再開する無限ループ。"""
        while True:
            if self.stop_event and self.stop_event.is_set():
                logger.info("シャットダウン要求を受け取りました。TikTokギフト監視を終了します。")
                break
            try:
                logger.info(f"{self.username} のTikTok Live監視を開始します...")
                # 既に接続されている場合の適切な処理 (TikTokLiveClientの仕様に依存)
                # TikTokLiveClientが内部で状態管理していることを期待。必要に応じてdisconnect()を呼ぶ。
                # if self.client.connected: # client.connected のようなプロパティがあるか確認
                #     logger.info("既に接続されているため、一度切断します。")
                #     await self.client.disconnect() # disconnectが非同期か同期か確認

                await self.client.start()
                # start()が正常に終了した場合 (通常はCtrl+Cなどで停止されるまでブロックするはず)
                # もしstart()が予期せず終了した場合、再接続ロジックが働く
                logger.warning(f"{self.username} のTikTok Live監視が停止しました。再接続を試みます...")

            except asyncio.CancelledError:
                logger.info("TikTokギフト検知タスクがキャンセルされました。")
                raise
            except ConnectionRefusedError as e:
                logger.error(f"TikTok Liveへの接続が拒否されました: {e}. {self.reconnect_delay}秒後に再試行します。", exc_info=True)
            except TimeoutError as e: # asyncio.TimeoutError or other specific TimeoutError
                logger.error(f"TikTok Liveへの接続がタイムアウトしました: {e}. {self.reconnect_delay}秒後に再試行します。", exc_info=True)
            # except SomeSpecificConnectionError as e: # TikTokLiveライブラリ固有の接続エラー
            #     logger.error(f"TikTok Live接続エラー: {e}. {self.reconnect_delay}秒後に再試行します。", exc_info=True)
            except Exception as e:
                # TikTokLiveClient.start()が投げる可能性のある他の主要な例外もここで捕捉することを推奨
                logger.error(f"TikTok Live監視中に予期せぬエラーが発生しました: {e}. {self.reconnect_delay}秒後に再試行します。", exc_info=True)

            # どのような状況でstart()が終了したかに関わらず、次の試行まで待機
            # (正常終了時もここに来る可能性があるため、シャットダウン要求時以外は再試行する)
            await asyncio.sleep(self.reconnect_delay)
