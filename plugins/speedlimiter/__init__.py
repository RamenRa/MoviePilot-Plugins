import ipaddress
from typing import List, Tuple, Dict, Any

from app.core.config import settings
from app.core.event import eventmanager, Event
from app.log import logger
from app.modules.emby import Emby
from app.modules.jellyfin import Jellyfin
from app.modules.plex import Plex
from app.modules.qbittorrent import Qbittorrent
from app.modules.transmission import Transmission
from app.plugins import _PluginBase
from app.schemas import NotificationType, WebhookEventInfo
from app.schemas.types import EventType
from app.utils.ip import IpUtils


class SpeedLimiter(_PluginBase):
    # 插件名称
    plugin_name = "播放限速"
    # 插件描述
    plugin_desc = "外网播放媒体库视频时，自动对下载器进行限速。"
    # 插件图标
    plugin_icon = "Librespeed_A.png"
    # 插件版本
    plugin_version = "1.2.1"
    # 插件作者
    plugin_author = "Shurelol"
    # 作者主页
    author_url = "https://github.com/Shurelol"
    # 插件配置项ID前缀
    plugin_config_prefix = "speedlimit_"
    # 加载顺序
    plugin_order = 11
    # 可使用的用户级别
    auth_level = 1

    # 私有属性
    _scheduler = None
    _qb = None
    _tr = None
    _enabled: bool = False
    _notify: bool = False
    _interval: int = 60
    _downloader: list = []
    _play_up_speed: float = 0
    _play_down_speed: float = 0
    _noplay_up_speed: float = 0
    _noplay_down_speed: float = 0
    _bandwidth: float = 0
    _allocation_ratio: str = ""
    _auto_limit: bool = False
    _limit_enabled: bool = False
    # 不限速地址
    _unlimited_ips = {}
    # 当前限速状态
    _current_state = ""
    _exclude_path = ""

    def init_plugin(self, config: dict = None):
        # 读取配置
        if config:
            self._enabled = config.get("enabled")
            self._notify = config.get("notify")
            self._play_up_speed = float(config.get("play_up_speed")) if config.get("play_up_speed") else 0
            self._play_down_speed = float(config.get("play_down_speed")) if config.get("play_down_speed") else 0
            self._noplay_up_speed = float(config.get("noplay_up_speed")) if config.get("noplay_up_speed") else 0
            self._noplay_down_speed = float(config.get("noplay_down_speed")) if config.get("noplay_down_speed") else 0
            self._current_state = f"U:{self._noplay_up_speed},D:{self._noplay_down_speed}"
            self._exclude_path = config.get("exclude_path")

            try:
                # 总带宽
                self._bandwidth = int(float(config.get("bandwidth") or 0)) * 1000000
                # 自动限速开关
                if self._bandwidth > 0:
                    self._auto_limit = True
                else:
                    self._auto_limit = False
            except Exception as e:
                logger.error(f"智能限速上行带宽设置错误：{str(e)}")
                self._bandwidth = 0

            # 限速服务开关
            self._limit_enabled = True if (self._play_up_speed
                                           or self._play_down_speed
                                           or self._auto_limit) else False
            self._allocation_ratio = config.get("allocation_ratio") or ""
            # 不限速地址
            self._unlimited_ips["ipv4"] = config.get("ipv4") or ""
            self._unlimited_ips["ipv6"] = config.get("ipv6") or ""

            self._downloader = config.get("downloader") or []
            if self._downloader:
                if 'qbittorrent' in self._downloader:
                    self._qb = Qbittorrent()
                if 'transmission' in self._downloader:
                    self._tr = Transmission()

    def get_state(self) -> bool:
        return self._enabled

    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        pass

    def get_api(self) -> List[Dict[str, Any]]:
        pass

    def get_service(self) -> List[Dict[str, Any]]:
        """
        注册插件公共服务
        [{
            "id": "服务ID",
            "name": "服务名称",
            "trigger": "触发器：cron/interval/date/CronTrigger.from_crontab()",
            "func": self.xxx,
            "kwargs": {} # 定时器参数
        }]
        """
        if self._enabled and self._limit_enabled and self._interval:
            return [
                {
                    "id": "SpeedLimiter",
                    "name": "播放限速检查服务",
                    "trigger": "interval",
                    "func": self.check_playing_sessions,
                    "kwargs": {"seconds": self._interval}
                }
            ]
        return []

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        return [
            {
                'component': 'VForm',
                'content': [
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 6
                                },
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'enabled',
                                            'label': '启用插件',
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 6
                                },
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'notify',
                                            'label': '发送通知',
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'content': [
                                    {
                                        'component': 'VSelect',
                                        'props': {
                                            'chips': True,
                                            'multiple': True,
                                            'model': 'downloader',
                                            'label': '下载器',
                                            'items': [
                                                {'title': 'Qbittorrent', 'value': 'qbittorrent'},
                                                {'title': 'Transmission', 'value': 'transmission'},
                                            ]
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 6
                                },
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'play_up_speed',
                                            'label': '播放限速（上传）',
                                            'placeholder': 'KB/s'
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 6
                                },
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'play_down_speed',
                                            'label': '播放限速（下载）',
                                            'placeholder': 'KB/s'
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 6
                                },
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'noplay_up_speed',
                                            'label': '未播放限速（上传）',
                                            'placeholder': 'KB/s'
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 6
                                },
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'noplay_down_speed',
                                            'label': '未播放限速（下载）',
                                            'placeholder': 'KB/s'
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 6
                                },
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'bandwidth',
                                            'label': '智能限速上行带宽',
                                            'placeholder': 'Mbps'
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 6
                                },
                                'content': [
                                    {
                                        'component': 'VSelect',
                                        'props': {
                                            'model': 'allocation_ratio',
                                            'label': '智能限速分配比例',
                                            'items': [
                                                {'title': '平均', 'value': ''},
                                                {'title': '1：9', 'value': '1:9'},
                                                {'title': '2：8', 'value': '2:8'},
                                                {'title': '3：7', 'value': '3:7'},
                                                {'title': '4：6', 'value': '4:6'},
                                                {'title': '6：4', 'value': '6:4'},
                                                {'title': '7：3', 'value': '7:3'},
                                                {'title': '8：2', 'value': '8:2'},
                                                {'title': '9：1', 'value': '9:1'},
                                            ]
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 6
                                },
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'ipv4',
                                            'label': '不限速地址范围（ipv4）',
                                            'placeholder': '留空默认不限速内网ipv4'
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 6
                                },
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'ipv6',
                                            'label': '不限速地址范围（ipv6）',
                                            'placeholder': '留空默认不限速内网ipv6'
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 6
                                },
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'exclude_path',
                                            'label': '不限速路径',
                                            'placeholder': '包含该路径的媒体不限速,多个请换行'
                                        }
                                    }
                                ]
                            }
                        ]
                    }
                ]
            }
        ], {
            "enabled": False,
            "notify": True,
            "downloader": [],
            "play_up_speed": None,
            "play_down_speed": None,
            "noplay_up_speed": None,
            "noplay_down_speed": None,
            "bandwidth": None,
            "allocation_ratio": "",
            "ipv4": "",
            "ipv6": "",
            "exclude_path": ""
        }

    def get_page(self) -> List[dict]:
        pass

    @eventmanager.register(EventType.WebhookMessage)
    def check_playing_sessions(self, event: Event = None):
        """
        检查播放会话
        """
        if not self._qb and not self._tr:
            return
        if not self._enabled:
            return
        if event:
            event_data: WebhookEventInfo = event.event_data
            if event_data.event not in [
                "playback.start",
                "PlaybackStart",
                "media.play",
                "media.stop",
                "PlaybackStop",
                "playback.stop"
            ]:
                return
        # 当前播放的总比特率
        total_bit_rate = 0
        # 媒体服务器类型，多个以,分隔
        if not settings.MEDIASERVER:
            return
        media_servers = settings.MEDIASERVER.split(',')
        # 查询所有媒体服务器状态
        for media_server in media_servers:
            # 查询播放中会话
            playing_sessions = []
            if media_server == "emby":
                req_url = "[HOST]emby/Sessions?api_key=[APIKEY]"
                try:
                    res = Emby().get_data(req_url)
                    if res and res.status_code == 200:
                        sessions = res.json()
                        for session in sessions:
                            if session.get("NowPlayingItem") and not session.get("PlayState", {}).get("IsPaused"):
                                if not self.__path_execluded(session.get("NowPlayingItem").get("Path")):
                                    playing_sessions.append(session)

                except Exception as e:
                    logger.error(f"获取Emby播放会话失败：{str(e)}")
                    continue
                # 计算有效比特率
                for session in playing_sessions:
                    # 设置了不限速范围则判断session ip是否在不限速范围内
                    if self._unlimited_ips["ipv4"] or self._unlimited_ips["ipv6"]:
                        if not self.__allow_access(self._unlimited_ips, session.get("RemoteEndPoint")) \
                                and session.get("NowPlayingItem", {}).get("MediaType") == "Video":
                            total_bit_rate += int(session.get("NowPlayingItem", {}).get("Bitrate") or 0)
                    # 未设置不限速范围，则默认不限速内网ip
                    elif not IpUtils.is_private_ip(session.get("RemoteEndPoint")) \
                            and session.get("NowPlayingItem", {}).get("MediaType") == "Video":
                        logger.debug(f"当前播放内容：{session.get('NowPlayingItem').get('FileName')}，"
                                     f"比特率：{int(session.get('NowPlayingItem', {}).get('Bitrate') or 0)}")
                        total_bit_rate += int(session.get("NowPlayingItem", {}).get("Bitrate") or 0)
            elif media_server == "jellyfin":
                req_url = "[HOST]Sessions?api_key=[APIKEY]"
                try:
                    res = Jellyfin().get_data(req_url)
                    if res and res.status_code == 200:
                        sessions = res.json()
                        for session in sessions:
                            if session.get("NowPlayingItem") and not session.get("PlayState", {}).get("IsPaused"):
                                if not self.__path_execluded(session.get("NowPlayingItem").get("Path")):
                                    playing_sessions.append(session)
                except Exception as e:
                    logger.error(f"获取Jellyfin播放会话失败：{str(e)}")
                    continue
                # 计算有效比特率
                for session in playing_sessions:
                    # 设置了不限速范围则判断session ip是否在不限速范围内
                    if self._unlimited_ips["ipv4"] or self._unlimited_ips["ipv6"]:
                        if not self.__allow_access(self._unlimited_ips, session.get("RemoteEndPoint")) \
                                and session.get("NowPlayingItem", {}).get("MediaType") == "Video":
                            media_streams = session.get("NowPlayingItem", {}).get("MediaStreams") or []
                            for media_stream in media_streams:
                                total_bit_rate += int(media_stream.get("BitRate") or 0)
                    # 未设置不限速范围，则默认不限速内网ip
                    elif not IpUtils.is_private_ip(session.get("RemoteEndPoint")) \
                            and session.get("NowPlayingItem", {}).get("MediaType") == "Video":
                        media_streams = session.get("NowPlayingItem", {}).get("MediaStreams") or []
                        for media_stream in media_streams:
                            total_bit_rate += int(media_stream.get("BitRate") or 0)
            elif media_server == "plex":
                _plex = Plex().get_plex()
                if _plex:
                    sessions = _plex.sessions()
                    for session in sessions:
                        bitrate = sum([m.bitrate or 0 for m in session.media])
                        playing_sessions.append({
                            "type": session.TAG,
                            "bitrate": bitrate,
                            "address": session.player.address
                        })
                    # 计算有效比特率
                    for session in playing_sessions:
                        # 设置了不限速范围则判断session ip是否在不限速范围内
                        if self._unlimited_ips["ipv4"] or self._unlimited_ips["ipv6"]:
                            if not self.__allow_access(self._unlimited_ips, session.get("address")) \
                                    and session.get("type") == "Video":
                                total_bit_rate += int(session.get("bitrate") or 0)
                        # 未设置不限速范围，则默认不限速内网ip
                        elif not IpUtils.is_private_ip(session.get("address")) \
                                and session.get("type") == "Video":
                            total_bit_rate += int(session.get("bitrate") or 0)

        if total_bit_rate:
            logger.debug(f"比特率总计：{total_bit_rate}")
            # 开启智能限速计算上传限速
            if self._auto_limit:
                play_up_speed = self.__calc_limit(total_bit_rate)
            else:
                play_up_speed = self._play_up_speed

            # 当前正在播放，开始限速
            logger.debug(f"上传限速：{play_up_speed} KB/s")
            self.__set_limiter(limit_type="播放", upload_limit=play_up_speed,
                               download_limit=self._play_down_speed)
        else:
            # 当前没有播放，取消限速
            self.__set_limiter(limit_type="未播放", upload_limit=self._noplay_up_speed,
                               download_limit=self._noplay_down_speed)

    def __path_execluded(self, path: str) -> bool:
        """
        判断是否在不限速路径内
        """
        if self._exclude_path:
            exclude_paths = self._exclude_path.split("\n")
            for exclude_path in exclude_paths:
                if exclude_path in path:
                    logger.info(f"{path} 在不限速路径：{exclude_path} 内，跳过限速")
                    return True
        return False
    
    def __calc_limit(self, total_bit_rate: float) -> float:
        """
        计算智能上传限速
        """
        # 当前总比特率大于总带宽，则设置为最低限速
        if not self._bandwidth or total_bit_rate > self._bandwidth:
            return 10
        return round((self._bandwidth - total_bit_rate) / 8 / 1024, 2)

    def __set_limiter(self, limit_type: str, upload_limit: float, download_limit: float):
        """
        设置限速
        """
        if not self._qb and not self._tr:
            return
        state = f"U:{upload_limit},D:{download_limit}"
        if self._current_state == state:
            # 限速状态没有改变
            return
        else:
            self._current_state = state
            
        try:
            cnt = 0
            text = ""
            for download in self._downloader:
                if cnt != 0:
                    text = f"{text}\n===================="
                text = f"{text}\n下载器：{download}"
                upload_limit_final = 0
                if self._auto_limit and limit_type == "播放":
                    # 开启了播放智能限速
                    if len(self._downloader) == 1:
                        # 只有一个下载器
                        upload_limit_final = int(upload_limit)
                    else:
                        # 多个下载器
                        if not self._allocation_ratio:
                            # 平均
                            upload_limit_final = int(upload_limit / len(self._downloader))
                        else:
                            # 按比例
                            allocation_count = sum([int(i) for i in self._allocation_ratio.split(":")])
                            upload_limit_final = int(upload_limit * int(self._allocation_ratio.split(":")[cnt]) / allocation_count)
                            logger.debug(f"下载器：{download} 分配比例：{self._allocation_ratio.split(':')[cnt]}/{allocation_count} 分配上传限速：{upload_limit_final} KB/s")
                            cnt += 1
                if upload_limit_final:
                    text = f"{text}\n上传：{upload_limit_final} KB/s"
                else:
                    text = f"{text}\n上传：未限速"
                if download_limit:
                    text = f"{text}\n下载：{download_limit} KB/s"
                else:
                    text = f"{text}\n下载：未限速"
                if str(download) == 'qbittorrent':
                    if self._qb:
                        self._qb.set_speed_limit(download_limit=download_limit, upload_limit=upload_limit_final)
                else:
                    if self._tr:
                        self._tr.set_speed_limit(download_limit=download_limit, upload_limit=upload_limit_final)
            # 发送通知
            self._notify_message(text, bool(upload_limit or download_limit), limit_type)
        except Exception as e:
            logger.error(f"设置限速失败：{str(e)}")

    def _notify_message(self, text: str, is_limit: bool, limit_type: str):
        """
        发送通知
        """
        if self._notify:
            title = "【播放限速】"
            if is_limit:
                subtitle = f"{limit_type}，开始限速"
                self.post_message(
                    mtype=NotificationType.MediaServer,
                    title=title,
                    text=f"{subtitle}\n{text}"
                )
            else:
                self.post_message(
                    mtype=NotificationType.MediaServer,
                    title=title,
                    text=f"{limit_type}，取消限速"
                )

    @staticmethod
    def __allow_access(allow_ips: dict, ip: str) -> bool:
        """
        判断IP是否合法
        :param allow_ips: 充许的IP范围 {"ipv4":, "ipv6":}
        :param ip: 需要检查的ip
        """
        if not allow_ips:
            return True
        try:
            ipaddr = ipaddress.ip_address(ip)
            if ipaddr.version == 4:
                if not allow_ips.get('ipv4'):
                    return True
                allow_ipv4s = allow_ips.get('ipv4').split(",")
                for allow_ipv4 in allow_ipv4s:
                    if ipaddr in ipaddress.ip_network(allow_ipv4, strict=False):
                        return True
            elif ipaddr.ipv4_mapped:
                if not allow_ips.get('ipv4'):
                    return True
                allow_ipv4s = allow_ips.get('ipv4').split(",")
                for allow_ipv4 in allow_ipv4s:
                    if ipaddr.ipv4_mapped in ipaddress.ip_network(allow_ipv4, strict=False):
                        return True
            else:
                if not allow_ips.get('ipv6'):
                    return True
                allow_ipv6s = allow_ips.get('ipv6').split(",")
                for allow_ipv6 in allow_ipv6s:
                    if ipaddr in ipaddress.ip_network(allow_ipv6, strict=False):
                        return True
        except Exception as err:
            print(str(err))
            return False
        return False

    def stop_service(self):
        pass
