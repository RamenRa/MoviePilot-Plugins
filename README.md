## 简介 v1.7.x
***
* 在原[suraxiuxiu库](https://github.com/suraxiuxiu/MoviePilot-Plugins/)的基础上添加了第三方api。功能：定时检查公网IP覆盖填写到企业微信“可信IP”列表中。确保微信通知可用<br>

* 第三方api主要缓解：因没有及时扫码，公网IP变动，Cookie也失效，导致企微应用无法发送通知。（MPv1可配置第三方为备用，企业微信发送失败时会自动尝试第三方）
* 使用第三方通知时，支持"IP变动后通知"，应对Cookie失效过快，但IP变动慢的环境。变相延长Cookie时长。
* 从指定URL获取IP：面向MoviePoilt部署在翻墙环境的用户
* 支持检测多网络出口(从URL获取)：面向iKuai随机分流或MP网络出口会跳的多网络出口用户。

### 可选的通知
* 企业微信应用、[Server酱](https://sct.ftqq.com/sendkey)、[AnPush](https://anpush.com/push/tool)、[PushPlus推送](https://www.pushplus.plus/push1.html)。配置任意一个后才可以正常使用`/push_qr`<br>

### 远程命令 /push_qr
* 直接在企业微信应用发送`/push_qr`，MoviePoilt收到后插件开始推送登录二维码。也可以用来**测试通知是否可用**
* MP可能会因网络波动没有收到推送命令，尤其是使用Cloudflare隧道的用户。多发送几次即可。收没收到以**MP的日志为准**。


## 使用说明 v1.7.x
***
* 需要获取一个参数和配置CookieCloud。如果收到验证码，请以`?`结尾发送到企业微信应用。如:`110103？`

1. 获取应用ID，图中地址栏的末尾**红线部分就是应用ID**
![image](https://www.helloimg.com/i/2024/11/15/67369975b7769.jpg)

2. 启用CookieCloud，在MoviePilot的`设定`→`站点`中勾选 **“启用本地Cookiecloud服务器”**，即使用内建CookieCloud
* 可以不启用，要取消勾选插件配置中的`使用CookieCloud`开关，这样MP重启后Cookie就会失效。
![image](https://www.helloimg.com/i/2024/11/15/67369975dd82f.jpg)

3. 使用企业微信应用通知示例。使用第三方推送则在"通知方式"中填写相应Token/API。
* 多个通知以`||`分隔，在MoviePilotV2使用多个通知时，微信作为首选其余通知方式会失效(但可以启用"IP变动后通知"功能，通知逻辑就变成：Cookie失效且公网IP变动时发送通知)。[配置方式](https://github.com/RamenRa/MoviePilot-Plugins#%E5%85%B3%E4%BA%8E%E5%BE%AE%E4%BF%A1%E4%BD%9C%E4%B8%BA%E4%B8%BB%E9%80%9A%E7%9F%A5%E7%AC%AC%E4%B8%89%E6%96%B9%E4%BD%9C%E4%B8%BA%E5%A4%87%E7%94%A8) <br>
![image](https://www.helloimg.com/i/2024/11/15/6736997616de8.png) 
* 企业微信：填写`WeChat`，上图还指定了微信的userid[非必填]。请确保企微应用已可以**正常交互** <br>
* Server酱：填写SCT开头的SendKey，支持Server<sup>3</sup>(虽说支持系统级推送，但扫码体验并不好) <br>
* AnPush：填写 `通道ID,API` ，在左侧`通道配置`找到通道ID <br>
* PushPlus：填写一键复制的Token <br>

<br>

## 一些问题和其他功能合集
***
### 关于微信作为主通知，第三方作为备用
* 只支持MoviePilot V1，在微信发送消息失败时，自动尝试使用下一个通知
* 在"通知方式"一栏填写：`WeChat,WangFeng||SCTxxx`代表配置微信和Server酱。参考以上说明，每个通知方式以`||`隔开

### 关于从指定URL获取IP 
* 在"应用ID"一栏填写：`应用ID,应用ID2||https://myip.net,https://ip138.net` [图片示例](https://www.helloimg.com/i/2024/12/22/67681feb4e0af.jpg)

### 关于Server<sup>3</sup>二维码推送到服务号
* 填写 `Server3的key,普通key` 实现将失效通知发给Server<sup>3</sup>系统级推送，二维码发给服务号

### 关于多网络出口检查(MP的出口IP会跳动才使用，否则不建议使用)
* 已知问题：第一次检查IP大概率会失败，首次安装可能要到**第三轮**检测后才正常(使用"立即检测一次"的不算)。
* v1.7.1的"立即检测一次"功能实际未生效。修复命令如下，运行后重启MP。
```
docker exec <MP的容器名称> sed -i 's/def write_wan2_ip(self):/def write_wan2_ip(self, event: Event = None):/' /app/app/plugins/dynamicwechat/__init__.py
```
* 原理是从URL获取IP：[orz.tools](https://ip.orz.tools/) [m27.tech](https://ip.m27.tech/) [skk.moe](https://ip.skk.moe/multi)。从这些URL能获取到多线路的IP，插件才能获取到。
* 在"应用ID"一栏填写：`应用ID||wan2`。[图片示例](https://www.helloimg.com/i/2024/12/22/67681feb3e0a4.jpg)
### 关于Cookie失效过快缓解方案
* 如果你的IP变动很慢，可以尝试配置第三方通知后打开"IP变动后通知"。即真正无法使用企业微信通知时发送通知，二维码会发到第三方通知。
* 在"通知方式"一栏至少填写一个可用的第三方token保存后，重新进入配置面板打开"IP变动后通知"开关。[图片示例](https://www.helloimg.com/i/2024/12/22/67681feb8299a.png)<br>

## 问题反馈
***
* 先查看[已关闭的Issuse](https://github.com/RamenRa/MoviePilot-Plugins/issues?q=is%3Aissue%20state%3Aclosed)和[Issuse](https://github.com/RamenRa/MoviePilot-Plugins/issues)是否有相同问题，确认没有后，在[Issuse](https://github.com/RamenRa/MoviePilot-Plugins/issues)右上角新建问题。附上插件设置和日志截图,日志中相同的错误重复输出的，重点关注第一次出现错误和最后一次的地方。 <br>
<p align="center"> ⭐ :point_left: ⭐ :point_right: ⭐ :point_left: ⭐ :point_right: ⭐ :point_left: ⭐ :point_right: ⭐ :point_left: ⭐ :point_right: ⭐ :point_left: ⭐ </p>


