# TunnelYield：MoviePilot 插件与 Tunnel API 采集端

有人使用隧道或播放视频时，为下载器让出带宽；需求解除后，恢复本插件暂停的任务或原限速。

前七部分面向有 Linux 基础的用户，第八部分供 AI 与维护者查阅。欢迎上传本文和脱敏日志向 AI 提问；不要上传真实密钥、完整个人配置或 VPN 证书。

示例约定：所有地址和名称均为占位符或虚构示例。`192.0.2.0/24`、`198.51.100.0/24`、`203.0.113.0/24` 是文档示例网段，不能直接作为实际连接地址；部署时替换为自己的地址。文中的客户端、采集端 ID 与节点名不来自任何用户环境。软件默认值另有明确标注。

适用范围：MoviePilot V3 插件、当前主从版采集端与安装器。若现场文件和文档不同，以当前源码为准。IKEv2 的兼容目标仅限下文声明的项目 Docker 部署，其他环境未测试。

目录：

- 第一部分：选型与数据流
- 第二部分：安装、更新与主从配置
- 第三部分：参数设置与预期现象
- 第四部分：Jellyfin/Emby 与 Webhook
- 第五部分：维护、改名与隐私
- 第六部分：排障与防火墙
- 第七部分：验收与验证范围
- 第八部分：面向 AI 的技术参考

## 第一部分：选型与数据流

### 1.1 按场景选择

| 场景 | 选择 | 数据流 |
|---|---|---|
| 监测 MP 已登记的 Jellyfin/Emby | 插件内开启媒体监测，不安装采集端 | 媒体服务器 → MP 插件 → 下载器 |
| 隧道都在一台机器 | standalone | 本地驱动 → 采集端 → MP → 下载器 |
| 隧道分散在多台机器 | 一个 master，其他 slave | 主端查询从端 → 统一快照 → MP → 下载器 |
| 从采集机查询 Jellyfin，生成播放虚拟节点 | 采集端 jellyfin 驱动 | Jellyfin → Provider → 快照 → MP |

当前内置下载器适配为 qBittorrent，可多选。采集端不直接操作下载器。

隧道监测和插件内置媒体监测是“或”关系：任一来源要求让位就保持；相关保持条件全部解除才恢复。采集端 jellyfin 虚拟节点属于外部快照，与插件内置媒体监测不共用排除规则。

### 1.2 角色与传输方式

| ROLE | 采集范围 | 对接 MP | WORK_MODE |
|---|---|---|---|
| standalone | 本机一个或多个驱动 | 自己对接 | webhook / poll |
| master | 本地驱动及多个从端，也可仅汇总远端 | 主端统一对接 | webhook / poll |
| slave | 只采本机，不再汇总其他采集端 | 不直接对接 | 固定 poll |

```text
主端本地驱动 ─────┐
从端 A ←主端查询──┼→ master 统一快照/seq ─Webhook或MP轮询→ MP → 下载器
从端 B ←主端查询──┘
```

- webhook：后台采集，变化推送、失败重试、定期保活。
- poll：按 HTTP 查询触发采集，短时复用缓存，不启动后台采集推送循环。
- master+webhook 中，主从之间仍是主端查询，不是从端推送。
- master+poll 只有被查询时才推进汇总；REMOTE_INTERVAL=10 不代表无人查询时也每10秒运行。
- 当前仅支持一层主从，不接收其他 master、standalone 或旧版匿名服务作为 slave。

## 第二部分：安装、更新与主从配置

### 2.1 文件与路径

```text
采集端源码目录/
├── tunnel_api.py
├── Tunnel_API_install.sh
├── installer_ui.py
├── readme.md
└── providers/
    ├── __init__.py
    ├── easytier.py
    ├── jellyfin.py
    ├── wireguard_未适配.py       # 按发布包提供
    └── ikev2.py           # 使用IKEv2时添加
```

MP 插件是独立代码，通常位于插件目录的 `tunnelyield/__init__.py`，不要与 `providers/__init__.py` 混淆。

| 项目 | OpenWrt / ImmortalWrt | Ubuntu / Debian |
|---|---|---|
| 安装目录 | /etc/tunnel-api/ | /opt/tunnel-api/ |
| 用户配置 | 安装目录/config.json | 安装目录/config.json |
| 序号状态 | 安装目录/tunnel_api_state.json | 安装目录/tunnel_api_state.json |
| 服务管理 | procd：tunnel-api | systemd：tunnel-api |

配套文件保持一致，要求 Python 3.8+。安装器可补装所需 Python 标准库和证书，但不安装隧道软件、Docker 或 Jellyfin。

```sh
# 在源码目录执行，Ubuntu/Debian可加sudo
sh ./Tunnel_API_install.sh
```

只选本机实际使用的驱动，多选用英文逗号分隔。主端不采本地时输入 `none`，但必须至少配置一个从端。

### 2.2 单机部署

1. 选择 standalone，再选 webhook 或 poll。
2. 单机不询问来源ID：新安装使用软件默认值 local，更新保留有效旧值。
3. 选择驱动，填参数、SECRET及必要的 MP 连接信息。
4. 试采成功后输入 yes 部署，按最终“配置与维护”板块填写 MP。

只用 MP 内置媒体监测则不需安装采集端，插件监控主机列表留空即可单独运行媒体监测。

### 2.3 主从部署

1. 先确定 MP、主端、所有从端共用的 SECRET。
2. 安装 slave：设置唯一ID、驱动、查询地址和端口，不填 MP Webhook/API_TOKEN。
3. 安装 master：填写主端ID和从端ID/根URL；可同时启用本地驱动。
4. 所有从端试采通过后确认部署。
5. MP 只连接主端，监控列表使用主端输出的最终节点名。

以下为虚构示例，非真实部署数据：

```text
从端：COLLECTOR_ID = collector-a，查询地址 http://192.0.2.20:8080/
主端添加：id = collector-a，url = http://192.0.2.20:8080/
主端自身：COLLECTOR_ID = collector-main
```

各端监听端口不必相同，远端URL必须填写从端实际端口。`0.0.0.0` 是监听设置，不是访问目标。

### 2.4 密钥、ID与节点名

- SECRET：所有采集端与 MP 插件一致；主端访问从端也复用它，不设逐端密钥。
- MP_API_KEY：MoviePilot API_TOKEN，仅推送发布者需要，不是 SECRET。
- Jellyfin API Key：访问 Jellyfin 的凭据，与上面两项不同。
- COLLECTOR_ID：采集器身份，主从配置须精确匹配、区分大小写，同一拓扑内不能重复。

主端输出固定来源后缀，虚构示例如下：

```text
node-a--collector-main.easytier
client-a--collector-a.ikev2
jellyfin-playback--collector-b.jellyfin
```

格式为 `原主机名--采集端ID.驱动名`；其中 jellyfin-playback 是软件默认虚拟节点名。主端所有节点都追加来源，避免一个来源消失后另一个节点又改回原名；对外 ipv4 匹配字段置空，原地址保留在 original_ipv4。

主从模式 MP 用完整主机名，不用原IP。单机沿用原命名，冲突时可能追加驱动后缀，重名IP不作为匹配键。以最终摘要/查询结果为准，不要选择采集端自身恒在线节点。

### 2.5 更新与迁移

- 覆盖源码目录的配套文件，保留 providers，再运行安装器；只改源码不等于部署。
- 当前安装器要求所有从端试采成功，任一网络、密钥、ID或协议失败都会中止更新，原服务不变。
- 本地驱动首次仅启用试采成功者；更新时旧的暂时失败驱动保留重试，新失败驱动不启用。
- 驱动目录合并、旧文件备份不代表自动回滚；备份可能含密钥，不要公开。
- 单机改主从会改变监控名称。建议先停用 MP 插件并核对恢复，再切换拓扑、停止其他独立发布者、修改监控列表后启用。
- 不删除或回退 seq 文件。安装器继承本机安装/源码目录较大序号，不读取其他机器；曾有多个发布者时应核对历史最大值。

## 第三部分：参数设置与预期现象

### 3.1 MP 插件参数

默认值以当前插件表单为准，已有配置不会自动重设。

| 参数 | 默认 | 设置后的行为 |
|---|---|---|
| 启用插件 | 关 | 开始监测；关闭并保存会尝试恢复仍有记录的暂停任务与原限速 |
| 上线通知 | 开 | 控制上线/播放开始通知，不影响控制动作；两类冷却独立 |
| 立即检测一次 | 关 | 保存后安排一次同步，执行后复位，不重置历史状态 |
| 下载器 | 空列表 | 可多选qBittorrent；取消勾选并保存即放弃其控制/恢复记录，不先自动恢复 |
| 检测方式 | Webhook | 与外部发布者WORK_MODE对应，不控制内置媒体查询的传输方式 |
| 通信密钥 | 空 | 与采集端SECRET一致；空值不接受推送 |
| 采集端API地址 | 空 | poll必填；webhook用于启动同步/主动心跳，主从只填主端 |
| API超时 | 10秒 | 查询失败不等于离线；无效非正值回退默认 |
| 检测/补偿周期 | `*/1 * * * *` | poll查采集端；webhook维护下载器；内置媒体查询也以此兜底 |
| 主动心跳 | 300秒 | webhook且有API地址时主动查询；填0关闭，留空不等于关闭 |
| 采集端失联释放 | 60分钟 | 长期无有效快照后尝试解除隧道让位；0关闭，正值最少30分钟 |
| 监控主机列表 | 空 | 每行IP或完整主机名；空列表不启用隧道主机判定 |
| 让位方式 | 暂停上传任务 | 只暂停当次有上传流量的任务，也可选全局限速 |
| 上传/下载限速 | 512 / 0 KB/s | 仅限速方式使用；0为该方向不限速，上传填0不能释放上传带宽 |
| 监测Jellyfin/Emby | 关 | 复用MP登记的媒体服务器，不受监控主机列表影响 |
| 不限速地址范围 | 空 | 内置媒体专用。空时按ipaddress属性排除私有/本机/链路本地；填后仅按显式规则 |
| 不限速路径 | 空 | 内置媒体专用，每行一个包含匹配项，匹配文件路径即不触发 |

地址规则支持IPv4/IPv6、CIDR、起始-结束区间，多个逗号分隔，不是字符串前缀匹配。需要排除特定隧道网段时显式填写，不把所有“看似内网”的地址一概视作默认排除。

主机匹配区分大小写，快照缺席节点按离线处理。主从必须填最终主机名。空检测周期会回退默认，停用请用插件开关。

### 3.2 采集端公共参数

| 字段 | 默认/范围 | 效果 |
|---|---|---|
| ROLE | standalone | 单机/主端/从端 |
| COLLECTOR_ID | 核心默认local | 单机安装隐藏；主从交互填写，更新保留旧值 |
| ENABLED_PROVIDERS | [] | 本机驱动列表；空仅允许有远端的master |
| WORK_MODE | webhook | webhook后台采集；poll按请求采集；slave固定poll |
| SECRET | 必填 | 可见ASCII，无空白；全局统一，主从填写已有密钥 |
| WEBHOOK_URL | MP插件地址 | `http://MP地址:端口/api/v1/plugin/TunnelYield/webhook` |
| MP_API_KEY | 推送端必填 | MP API_TOKEN，poll/slave不使用推送凭据 |
| ENABLE_QUERY_API | true | poll/slave必须开启，webhook可关闭 |
| LISTEN_ADDR / LISTEN_PORT | 0.0.0.0 / 8080 | 当前服务监听IPv4，端口1~65535；安装器不开放防火墙 |
| SCAN_INTERVAL | 2秒；1~3600 | webhook一轮结束后的等待；poll查询缓存窗口，不是后台采集周期 |
| KEEPALIVE_INTERVAL | 300秒；60~1500 | webhook保活重推，poll不使用但配置仍需合法 |
| REMOTE_SOURCES | [] | master的id/url数组，最多16个，无逐端密钥 |
| REMOTE_INTERVAL | 10秒；1~300 | 主端访问从端的间隔，不大于主端TTL；实际受轮次和耗时影响 |
| REMOTE_TIMEOUT | 3秒；1~30 | 主端网络请求超时；首次慢查询可按实测调10或15秒 |
| SOURCE_CACHE_TTL | 300秒；1~900 | 单来源旧状态期限，硬上限15分钟，不是MP恢复承诺 |

远端使用http/https根URL，不含账户、查询参数、片段；禁止重定向，保留HTTPS证书验证。主端可纯汇总，但不级联其他主端。

### 3.3 常见现象对照

| 设置/情况 | 预期 |
|---|---|
| webhook、SCAN_INTERVAL=8、KEEPALIVE=300 | 每轮后约等8秒；变化推送，约5分钟保活，不是每8秒都发 |
| slave查询缓存5秒，主端间隔10秒 | 通常随主端查询采集；无人查询不自行每5秒采集 |
| master+poll，MP每分钟查询 | REMOTE_INTERVAL=10不能保证10秒内发现变化 |
| 有效保活、状态未变 | 可刷新同步信息；下载器维护按补偿周期，不每次保活扫种子 |
| 主机在线、没有上传流量 | 可同时显示在线与“无插件暂停记录” |
| 隧道离线、媒体仍播放 | 继续让位 |
| 部分失败、TTL=300 | 最后状态最多沿用5分钟；其他来源健康时到期按策略释放 |
| 所有来源失败/主端停止 | 不发健康快照；MP恢复受失联配置及媒体状态约束 |

### 3.4 驱动参数

#### EasyTier

| 字段 | 默认 | 说明 |
|---|---|---|
| EASYTIER_CLI | easytier-cli | 安装时选实际可执行路径；读peer，详情可读node/route |

按节点状态判定，不做Ping探测。Local节点恒在线，不加入MP监控名单。

#### WireGuard（发布包包含该驱动时）

| 字段 | 默认 | 说明 |
|---|---|---|
| WG_CLI | wg | 命令路径 |
| WG_INTERFACE | wg0 | 接口名 |
| WG_HANDSHAKE_TIMEOUT | 180秒 | 最近握手在阈值内判在线，不代表正在播放视频 |
| WG_PEER_NAMES | {} | 公钥到显示名映射 |

长时间无握手可能判离线，需要持续在线判断时按环境配置客户端keepalive。接口UP不等于客户端在线。

#### Jellyfin Provider

| 字段 | 默认 | 说明 |
|---|---|---|
| JELLYFIN_URL / JELLYFIN_API_KEY | 必填 | 独立配置，不复用MP登记 |
| JELLYFIN_TIMEOUT | 5秒 | 网络超时 |
| JELLYFIN_USER_IDS | [] | 空为全部用户，否则仅这些用户ID |
| JELLYFIN_EXCLUDE_IPS | [] | IP/CIDR排除；空不排除，不自动排除内网 |
| JELLYFIN_INCLUDE_PAUSED | false | 暂停默认不触发，true时暂停视频仍触发 |
| JELLYFIN_SESSION_TIMEOUT | 0 | 0不检查新鲜度，正值按播放/活动时间检查，秒 |
| JELLYFIN_NODE_NAME | jellyfin-playback | 软件默认的播放虚拟节点名，可修改 |

仅视频；用户、暂停、地址和新鲜度共同决定状态。地址未知不自动排除，结构损坏则失败。当前不提供插件内置“不限速路径”参数。

#### IKEv2 Provider：限定兼容范围

本驱动明确适配 [hwdsl2/setup-ipsec-vpn](https://github.com/hwdsl2/setup-ipsec-vpn) 项目提供的 Docker 部署方式。

其他环境未测试，包括原生安装、其他项目/镜像及StrongSwan/swanctl等实现，不属于当前兼容承诺。代码保留非Docker参数入口，不代表已验证原生部署。这里的Docker指VPN服务器的部署方式，不要求采集服务本身也运行在Docker中。

| 字段 | 默认 | 说明 |
|---|---|---|
| IKEV2_DOCKER | true | 安装询问是否Docker，默认y；非Docker入口未测试 |
| IKEV2_DOCKER_CLI | docker | 宿主机Docker可执行路径 |
| IKEV2_CONTAINER | ipsec-vpn-server | 驱动默认容器名，按实际部署修改 |
| IKEV2_IPSEC_CLI / IKEV2_SCRIPT | ipsec / ikev2.sh | Docker容器内的命令名 |
| IKEV2_CONNECTION_PREFIX | ikev2-cp | 匹配相同名及该名前缀加连字符的连接 |
| IKEV2_TIMEOUT | 8秒；1~60 | 单个命令超时，不是整个主从链路超时 |
| IKEV2_CLIENT_CACHE_SECONDS | 60秒；0~3600 | 名单缓存，0关闭；每轮仍读trafficstatus |

- `--listclients`中valid表示证书状态，不等于在线；活动ESP状态才说明隧道活动。
- 主机名只取客户端身份，地址只取`lease=`；不把外层传输地址用于节点匹配。
- 无活动会话的已登记客户端输出离线、IP为空。优先按名称监控，避免租约变更。
- 多SA按客户端名合并；同名多个不同lease时仅用主机名匹配。
- 命令失败/未知格式返回采集失败，不伪造全部离线；自动命令不带`-i/-t`，不修改VPN。

虚构解析示例：客户端名为client-a，外层地址为192.0.2.50，`lease=198.51.100.10/32`，则节点只使用client-a和198.51.100.10。这些地址仅用于文档演示。

## 第四部分：Jellyfin/Emby 与 Webhook

### 4.1 两条媒体链路分别配置

| 项目 | MP内置媒体监测 | 采集端jellyfin驱动 |
|---|---|---|
| 连接信息 | 复用MP登记的Jellyfin/Emby | 独立Jellyfin URL/API Key |
| 默认地址排除 | 按插件默认规则排除私有/本机等地址 | 无，空列表不排除 |
| 路径排除 | 支持 | 当前无该参数 |
| 输出 | 独立媒体保持条件 | 在线/离线虚拟节点 |
| 触发 | MP周期查询＋媒体事件 | 采集端采集/主端查询 |

两条链同时开启时规则独立：内置监测排除某地址，不能阻止Provider虚拟节点触发。通常选一条即可，需同时用则先统一预期。

反代可能使会话IP一直显示代理地址。先核对真实会话IP、转发头和服务器可信代理设置，不盲目信任任意来源转发头。

### 4.2 Jellyfin Webhook 配置

此Webhook发送到MP宿主，不是采集端。它触发插件复查会话，不直接信任报文中的播放状态；只使用Provider时不能靠此Webhook绕过采集周期。

安装Jellyfin Webhook插件并重启，添加Generic Destination，按项目已有实测配置填写：

| 项目 | 填写 |
|---|---|
| Webhook URL | `http://<MP_HOST>:<MP_PORT>/api/v1/webhook/?token=<API_TOKEN>` |
| 状态 | Enable |
| 事件 | Playback Start、Playback Stop，其他按版本能力选择 |
| 媒体类型 | Movies、Episodes，其他视频按需 |
| Send All Properties | 不勾选，使用自定义模板 |
| Request Header：Key输入框 | `Content-Type` |
| Request Header：Value输入框 | `application/json` |

点击 Add Request Header 新增一条请求头。Key和Value是两个独立输入框，分别填写上面两项；不要把`Content-Type: application/json`整串填进Key。

宿主URL使用`/api/v1/webhook/`结尾斜杠，避免307重定向兼容性问题；不要误填采集插件的`/api/v1/plugin/TunnelYield/webhook`。

```json
{
  "NotificationType": "{{{NotificationType}}}",
  "ItemId": "{{{ItemId}}}",
  "ItemType": "{{{ItemType}}}",
  "Name": "{{{Name}}}",
  "SeriesName": "{{{SeriesName}}}",
  "NotificationUsername": "{{{NotificationUsername}}}",
  "DeviceName": "{{{DeviceName}}}",
  "ClientName": "{{{ClientName}}}",
  "RemoteEndPoint": "{{{RemoteEndPoint}}}"
}
```

保留周期查询兜底。事件正常时可缩短延迟，但不保证固定秒数；停止/暂停还受会话API更新影响。无Webhook保持每分钟检测，确认事件可靠后再按需放宽。

同目录`jellyfin-webhook-for-speedlimit.md`保存原配置记录；Emby仅参考，不能把Jellyfin实测等同于Emby已验证。URL token属于秘密，外发前脱敏。

## 第五部分：维护、改名与隐私

### 5.1 服务操作

| 操作 | OpenWrt / ImmortalWrt | Ubuntu / Debian |
|---|---|---|
| 状态 | `/etc/init.d/tunnel-api status` | `systemctl status tunnel-api` |
| 重启 | `/etc/init.d/tunnel-api restart` | `systemctl restart tunnel-api` |
| 停止 | `/etc/init.d/tunnel-api stop` | `systemctl stop tunnel-api` |
| 启动 | `/etc/init.d/tunnel-api start` | `systemctl start tunnel-api` |
| 日志 | `logread -e tunnel` | `journalctl -u tunnel-api -f` |
| 自启 | `/etc/init.d/tunnel-api enable` | `systemctl enable tunnel-api` |
| 取消自启 | `/etc/init.d/tunnel-api disable` | `systemctl disable tunnel-api` |

修改安装目录config.json后重启；取消自启不会停止当前进程，停止采集端也不等于立即恢复下载器。

### 5.2 下载器改名、删除与切换

1. 保留原下载器勾选，关闭“启用插件”并保存。
2. 核对任务和原限速已恢复，失败/待确认项先手动处理。
3. 再修改MP下载器名称或删除下载器，回插件选新名称后启用。

取消勾选并保存意味着放弃该下载器的控制和恢复记录，不先自动恢复；重新勾选不能找回已丢弃的原限速备份，风险由用户承担。刷新插件或调用stop_service生命周期钩子不等于明确停用流程。

### 5.3 通知与隐私

播放通知保留服务器名称/类型、用户名、设备或客户端名、客户端地址、会话数量和让位方式；缺失显示`-`。不发送实际媒体名或文件路径。

本地详情、日志、发往MP宿主的媒体Webhook仍可能包含媒体信息。通知脱敏不是全系统脱敏。外发前删除真实SECRET、MP_API_KEY、Jellyfin Key、Webhook token、证书/私钥及个人设备/网络标识。

公开文档和提问示例使用占位符或文档专用地址，不复制用户现场IP、MAC、网段、设备名、客户端名、节点名、来源ID或日志中的登录提示符。

## 第六部分：排障与防火墙

### 6.1 分层判断

| 现象 | 检查方向 |
|---|---|
| Host is unreachable / Errno 113 | 路径、邻居、按端口REJECT；不一定缺路由 |
| Ping通、TCP不通 | TCP端口与策略，Ping不能证明HTTP可达 |
| Connection refused | 目标监听、地址和服务状态 |
| 超时 | 区分TCP不通与驱动采集过慢，不先改密钥 |
| 403，正文forbidden | 采集端SECRET不一致 |
| 503 | 未就绪、首次等待、全部失败等，不是全部节点离线 |
| HTTP200但主端拒绝 | collector角色/ID/协议、source_states或错误服务 |
| 主端统一报“来源失败” | 当前隐藏底层细节，单独测HTTP状态、耗时及元信息 |
| HTTP200但MP没变化 | 读message是否旧seq/冲突被忽略，再查监控名和条件 |
| 播放不让位 | 区分媒体链路、过滤条件、反代IP、暂停/音频及周期 |
| 不恢复 | 检查其他来源、媒体保持、未知超时、恢复失败与待确认任务 |

示例检查命令中的地址是文档地址，必须替换：

```sh
# 主端：检查到从端的路径
ip route get 192.0.2.20
ping -c 3 192.0.2.20
ip neigh show

# 从端：示例使用软件默认查询端口8080
ip -4 addr show
ss -lntp | grep ':8080'
systemctl status tunnel-api --no-pager -l
journalctl -u tunnel-api -n 50 --no-pager
```

主从ID一致不代表可达，服务running不代表驱动健康。首次IKEv2请求可能执行两条Docker命令；网络通后再看耗时，必要时增加主端REMOTE_TIMEOUT，不用超时设置掩盖Errno 113。

请求密钥不要直接写进命令历史；使用隐藏输入，再通过X-Tunnel-Secret头传入。未认证GET返回403能证明HTTP服务可达，密钥及数据校验是下一层。

### 6.2 firewalld：iptables ACCEPT不代表最终允许

典型情况是Ping正常、Python监听所有IPv4地址、iptables INPUT为ACCEPT，但`nft list ruleset`中还有firewalld表；入口区域允许ICMP/SSH后拒绝其他流量，TCP查询仍会返回不可达。

```sh
iptables -nvL INPUT --line-numbers
iptables -S
nft list ruleset
firewall-cmd --get-active-zones
```

先确认实际入口区域，再按主端IP和查询端口最小放行。以下全部为虚构地址示例，部署前替换地址、区域和端口：

```sh
# 示例：主端192.0.2.10 → 从端192.0.2.20:8080，入口区域public
RULE='rule family="ipv4" source address="192.0.2.10/32" destination address="192.0.2.20/32" port port="8080" protocol="tcp" accept'
firewall-cmd --zone=public --add-rich-rule="$RULE"
```

验证成功后，同一终端保存永久配置：

```sh
firewall-cmd --permanent --zone=public --add-rich-rule="$RULE"
firewall-cmd --zone=public --list-rich-rules
firewall-cmd --permanent --zone=public --list-rich-rules
```

运行规则已生效，不需为此立即reload；换Shell后须重新定义RULE。撤销时用同一规则配合`--remove-rich-rule`，永久规则另加`--permanent`。

不要清空防火墙、停掉整个firewalld或把所有来源放进trusted。采集程序监听在VPN容器之外时，不需要给VPN容器映射Python查询端口；无证据不盲目加静态路由。

## 第七部分：验收与验证范围

### 7.1 最小验收

1. 主从服务运行，主端能认证访问每个从端，ID和协议正确。
2. 监控设备上线，MP接受完整快照并让位。
3. 设备离线且其他来源无需保持，核对任务/原限速恢复。
4. 暂停方式用有真实上传流量的任务测试；限速方式先记原值。
5. 逐个中断来源，验证缓存期、到期策略和恢复；不要只测全部正常。
6. 所有来源失败时，按MP失联配置验收，不把900秒当最终恢复承诺。
7. 内置媒体用真实播放/停止验证，curl模拟事件只证明链路。

### 7.2 当前验证边界

- EasyTier、Jellyfin Provider已有用户实测反馈，不外推到所有部署。
- Jellyfin宿主Webhook有项目配置实测记录；Emby仍为参考。
- IKEv2兼容目标仅为[hwdsl2/setup-ipsec-vpn](https://github.com/hwdsl2/setup-ipsec-vpn)项目的Docker部署；其他环境未测试。解析样例和命令模拟测试不等于所有镜像版本及完整VPN→MP→下载器链路均验收通过。
- 主从/UI有模拟和本机HTTP定向测试，不代表所有系统、虚拟化和网络组合已验证。
- 重载、失联、僵尸会话、反代IP和下载器恢复失败仍应现场验证。
- README是操作/技术索引，不是所有环境兼容保证。

## 第八部分：面向 AI 的技术参考

### 8.1 阅读范围与证据优先级

优先顺序：当前源码/脱敏配置 → 当前运行证据 → 本文 → 历史说明。旧Provider包注释“只发布本轮成功源”不代表当前主从缓存行为，应看核心collect_snapshot。

本文结合主从核心、安装器、UI、MP V3插件的已确认行为、Jellyfin配置记录和IKEv2适配版本。现场实现如有变化，应重新核对，不沿用文档假设。

不要假设角色等于传输方式、HTTP200等于刚采成功、HTTP200等于MP已应用、running等于驱动健康或Ping通等于TCP通。

隐私要求：生成公开文档/示例时不得复用用户现场网段、地址、设备/节点/来源名称。使用占位符或文档专用地址，声明为虚构；软件内置默认值应明确标注。脱敏不止替换密钥。

### 8.2 文件职责与入口

| 文件/入口 | 职责 |
|---|---|
| 插件TunnelYield | 唯一控制与恢复记录所有者；配置、SDK调度/事件、隧道/媒体协调、通知与持久化 |
| list_providers / get_provider_module | 扫描providers/*.py，按合法模块名加载 |
| _all_config_defaults / validate_config | 合并默认值和校验；字段冲突报错，未启用驱动参数保留 |
| collect_snapshot | 本地/远端采集、来源缓存、初始等待、健康判断和身份标准化 |
| _collect_remote | 鉴权、协议/ID检查、来源年龄及deadline转换 |
| _collect_and_publish | 锁内采集汇总，成功后分配seq并更新_latest |
| process_once / watch_loop | 签名、重试退避、保活与循环节奏 |
| Handler.do_GET | 鉴权、缓存/按需采集、503及详情返回 |
| configure_cluster / configure_provider | 角色与来源配置；声明式或条件交互 |
| summary_text | 统一结束板块；MP配置或主端接入与维护操作分组 |

采集并发最多8任务，远端最多16。采集和查询更新共用锁，推送在锁外；同步HTTP服务和慢驱动会影响响应。配置周期不是严格墙钟周期，网络超时也不是整轮采集的硬时限。

### 8.3 Provider契约与安装器扩展

```python
DEFAULT_CONFIG = {"DRIVER_KEY": "..."}

def collect(config):
    # 成功为非空节点列表；失败返回None。
    return ([{"ipv4": "", "hostname": "node-a", "is_online": False}], raw)

# 可选：validate_config(config)
# 可选：CONFIG_FIELDS，声明类型/帮助/范围/choices
# 可选：configure(old, ask, integer, executable, tty)
# 可选：detail(raw, config) -> (ok, dict_or_none)
```

ipv4/hostname必须为字符串，is_online为严格bool，至少一种身份；完全重复身份报错。不要用空列表表达全部离线，保留已知节点并置false。核心另有节点数、响应大小等校验。

存在CONFIG_FIELDS时安装器优先通用表单；没有且有configure时走自定义钩子。IKEv2故意不声明CONFIG_FIELDS，以便按Docker/预留非Docker入口条件提问，并返回全部默认键，避免补问不适用字段。新驱动不应要求修改已验证驱动或在核心硬编码名称。

IKEv2兼容声明限于hwdsl2/setup-ipsec-vpn项目的Docker部署。代码有原生入口不能推导为原生已兼容，更不能外推到StrongSwan等其他实现。其他环境未测试。

IKEv2内部：_run使用参数数组、不启Shell；parse_clients校验名单/总数；parse_trafficstatus过滤连接、提取身份和lease；_catalog仅缓存名单，collect每轮读活动状态。原始输出不进对外详情；不把valid当在线，不扫描整行第一个IPv4作为客户端地址。

### 8.4 身份与命名空间

- COLLECTOR_ID是采集器身份，不是VPN客户端名；主从字符串精确匹配。
- 远端provider为`采集端ID.驱动名`，主端本地也带本机ID。
- master为所有节点固定加来源，清空对外ipv4；original_ipv4仅诊断。
- normalize_identities处理主机名/IP交叉碰撞。同来源同名不同身份可加稳定摘要，不能简单按主机名字典覆盖。
- 改来源ID影响MP节点名，与下载器名称绑定是两套机制。

### 8.5 主从与MP协议

MP仍使用schema=1，以下地址/身份/序号均为虚构，不用于初始化真实状态：

```json
{
  "schema": 1,
  "source": "multi",
  "seq": 10001,
  "timestamp": 1793530000,
  "peer_list": [
    {"ipv4":"","hostname":"client-a--collector-a.ikev2","is_online":true}
  ],
  "collector": {"protocol":1,"role":"master","id":"collector-main","cache_ttl":300}
}
```

source是数据类型，不是采集器ID。只有master/standalone发布序列参与同一MP实例的判序，不直接转发各slave的seq。

主端访问从端根URL加`?brief=1`，全局X-Tunnel-Secret鉴权；禁止重定向、保留HTTPS校验、响应最大4MiB。要求collector.protocol为整数1、role=slave、id与配置一致、cache_ttl在1~900，且source_states为有效驱动字典。

```json
{
  "collector": {"protocol":1,"role":"slave","id":"collector-a","cache_ttl":300},
  "source_states": {
    "ikev2": {
      "peer_list":[{"ipv4":"198.51.100.10","hostname":"client-a","is_online":true}],
      "age_seconds":250.0
    }
  }
}
```

上面只示意关键字段。未采到的驱动可为peer_list=null且age_seconds=null；其余年龄必须是非负有限数，不能是bool。主端导入source_states，不用顶层timestamp或seq替代来源新鲜度。

采集→MP插件：`/api/v1/plugin/TunnelYield/webhook`，同时使用X-API-KEY=MP_API_TOKEN与X-Tunnel-Secret=SECRET。

Jellyfin→MP宿主：`/api/v1/webhook/?token=API_TOKEN`，使用独立媒体事件协议。Jellyfin配置界面的Header是Key/Value两个输入框，Key=`Content-Type`、Value=`application/json`，不是单个文本字段。

### 8.6 缓存、健康与失联

```text
effective_age = 从端实际采集年龄 + 本次请求耗时
remaining     = min(主端TTL, 从端TTL) - effective_age
deadline      = 主端当前单调时钟 + remaining
```

- HTTP200、根快照更新时间、新seq不会给旧来源续期。
- 缓存寿命使用单调时钟，节点状态只在内存，重启不重授旧在线状态有效期。
- 成功导入的完整集合替换该远端成员，不永久保留已删除驱动。
- 部分失败：期限内保持；到期且其他来源健康时置false并标cache_expired，是策略释放而非现实离线证明。
- 没有健康来源或无节点：None/503，不用纯失败缓存持续刷新MP联络。未到下次远端查询时，可用此前查询成功且仍有效的来源。
- 冷启动：本地驱动无首次结果或远端无导入记录时，在TTL窗口内可阻止不完整发布；安装试采persist=False忽略此等待。
- 远端HTTP200但只有缺失/过期记录，不保证聚合可发布；安装器错误文字并不总揭示精确子原因。

| 计时器 | 控制对象 |
|---|---|
| SOURCE_CACHE_TTL 1~900秒 | 单来源旧在线状态 |
| SCAN_INTERVAL | 循环等待或poll查询缓存，不是失败TTL |
| REMOTE_INTERVAL / TIMEOUT | 主从访问节奏/网络超时 |
| IKEV2_CLIENT_CACHE_SECONDS | 客户端名单，不是活动SA |
| 插件判定有效期`max(heartbeat*2,1800)`秒 | 旧隧道判定能否授权补偿 |
| MP lost_release默认60分钟 | 全局无有效快照后的隧道释放 |
| MEDIA_UNKNOWN_HOLD=1800秒 | 内置媒体未知状态保持 |

来源缓存到期、补偿暂停和MP最终恢复是不同事件；其他来源/媒体仍可保持。失联释放为0不承诺自动恢复。配置重载重建本轮同步/计时资格，不是无副作用探测。

### 8.7 seq、重试和写盘

- 预留号段50000，成功汇总即可分配seq，不必已有网络推送；重启从持久化上限后继续。
- 使用临时文件和replace，不是逐帧fsync强事务；丢文件退到时间基准并告警，不以删文件解决旧版本拒绝。
- MP优先按seq判序，同seq同内容可重同步、同seq异内容冲突、旧seq忽略。不要让旧独立发布者继续干扰新拓扑。
- send_webhook要求HTTP200且success=true，但MP可返回“已接收未应用”；必须读message。
- 内容签名变化、未确认送达或保活到期才推送；指数退避上限60秒，后续以最新快照重试。
- MP状态包含恢复与判序信息，不能整文件删除；时间类变化有写盘节流，不用文件mtime判断每次内存同步。

### 8.8 MP控制与恢复记录

插件统一协调，不允许每个采集端各自恢复同一下载器。暂停需有效任务状态且upspeed>0，不批量暂停空闲做种；限速先读原值，读失败不等于原值0。

控制记录包含released、paused_hashes、pending_hashes、orig_limits、qb_key，按下载器名称分组。暂停意图先记录，成功与待确认分开，pending不自动恢复；原限速与恢复结果需读回，失败保留可重试记录。

取消勾选在配置加载时主动放弃记录，即使有未恢复内容也删除；不是“仅清空记录”或“离线再恢复旧下载器”。详情页只读，不因打开页面查询/写盘/清理。

类级锁串行化配置/动作；媒体查询在锁外，以配置代次和查询开始单调时间防过期覆盖。stop_service取消资格/一次性任务，不擅自恢复；明确关闭enabled并保存才进入停用恢复。

“上次检测”来自_last_check_info.time，是状态处理记录，不严格等于最后成功采集；失败、跳过或失联分支也可能写它。持续在线不自动刷新，正常补偿通常不改它；媒体另有_media_info，页面需重新取得后端数据。

### 8.9 媒体与隐私

内置媒体聚合MP登记的Jellyfin/Emby：任一有效播放保持；无有效播放但有查询失败，不等于全部空闲。未知有保持窗口，不能伪造空列表释放。

通知sessions项为server/type/user/device/ip/item；device有设备/客户端名兜底，item可能含真实剧集名。当前通知不拼接item或路径，但发送用户/设备/IP；不能描述为“只发数量”或“本地日志已全局脱敏”。

Provider Jellyfin独立处理用户、暂停、新鲜度和地址，默认不排除IP，结构异常即失败，不能套用内置媒体默认规则。

Webhook仅触发复查。模板来自项目配置记录，不保证所有版本/特殊字符均兼容；媒体名含引号或控制字符时验证JSON转义，不把未转义数据直接拼接到JSON。不要公开带token的URL。

### 8.10 AI排障与提问清单

```text
文件与配置是否部署
→ 角色、目标地址与监听
→ 路由/邻居/TCP，必要时过滤抓包
→ HTTP状态与鉴权
→ collector身份/协议、source_states及年龄
→ 驱动命令与解析
→ 聚合健康与MP判序
→ 监控名、媒体过滤和保持
→ 下载器执行、读回与恢复记录
```

先做只读检查：不因Errno113就加路由，不因iptables ACCEPT就断言无防火墙，不扩大到所有来源放行。没有证据不直接归因于虚拟化宿主、Docker或VPN；firewalld运行/永久配置分开验证，避免不必要reload影响网络。

向用户索取的最小资料：

- 角色、WORK_MODE、驱动、各端ID和脱敏URL/端口，标明在哪台机器执行。
- 非秘密配置、安装报错、对应服务日志。
- 监听、路由/邻居、TCP结果，必要时抓包摘要。
- HTTP状态、耗时、collector、来源名称/年龄，不索要真实SECRET。
- MP监控名单、媒体开关/排除规则、控制记录与实际上传状态。
- 是否重新部署、是否仍有其他独立发布者。

生成示例前再检查隐私：把现场地址、设备/节点/来源名换成独立虚构值，不只替换最后一个IP字节，也不沿用用户网段。代码默认名称与虚构示例应明确区分。IKEv2非Docker环境必须保留“未测试”边界，不因代码有选项而扩写为已支持。

可复制的提问模板：

> 请先阅读此README，再结合当前源码和脱敏日志排查。部署为［单机/主从］，角色与地址［...］，期望［...］，实际［...］。请区分网络、鉴权、来源新鲜度、MP判定和下载器执行层；先给最少只读检查，不默认关闭防火墙、删除seq或清空恢复记录。明确事实与假设，示例不要使用我的真实环境信息。

本文不授权AI执行外部写操作。更改服务、防火墙或控制记录前说明目标、影响与回退；交付代码声明测试范围，不把语法/模拟检查称作实际联调。
