# 抽卡记录（原神 · 崩铁 · 绝区零 · 鸣潮 · Windows 桌面软件）

一个自带窗口的桌面软件：读取你电脑上游戏留下的记录链接，取回官方的抽卡记录，在本机统计保底、出金，并长期保存。一个软件查多个游戏。

支持的游戏（均为国服）：

| 游戏 | 记录从哪里取 |
| --- | --- |
| 原神 | 游戏目录下的网页缓存 |
| 崩坏：星穹铁道 | 游戏目录下的网页缓存 |
| 绝区零 | 游戏目录下的网页缓存 |
| 鸣潮 | 游戏目录下的 `Client\Saved\Logs\Client.log`（加密日志，软件会自动解码） |

## 安装和使用

到 [Releases](https://github.com/xqf946/genshin-wish-log/releases) 页面下载：

- **`GenshinWishLog-Setup-x.y.z.exe`**：安装包。双击安装，会在开始菜单和桌面创建快捷方式，可以正常卸载。不需要管理员权限。
- **`GenshinWishLog-x.y.z-portable.zip`**：免安装版。解压到一个固定的文件夹，双击里面的 `GenshinWishLog.exe` 即可。

使用：

1. 在游戏里打开记录页（**每次更新前都要做**，链接有时效）：
   - 原神：「祈愿」→ 左下角「历史记录」
   - 崩铁：「跃迁」→「跃迁记录」
   - 绝区零：「调频」→「调频记录」
   - 鸣潮：「唤取」→「唤取记录」

   随便点开一个卡池翻一翻。
2. 打开本软件，在左侧选游戏，点右上角「更新记录」。想一次更新所有游戏，点左下角「更新全部游戏」，没装的游戏会自动跳过。

首次运行时 Windows 可能提示“Windows 已保护你的电脑”（SmartScreen）：这是因为程序没有付费的代码签名，点「更多信息」→「仍要运行」即可。部分杀毒软件也可能误报，需要的话把它加入信任。

系统要求：Windows 10 / 11（64 位）。窗口内容由 Microsoft Edge WebView2 运行时绘制，Win11 和绝大多数 Win10 已自带；如果没有，软件启动时会告诉你去哪里下载。

## 它是怎么工作的

**原神、崩铁、绝区零**（米哈游）：你在游戏里打开历史记录页时，游戏内置的网页会请求官方接口，这个请求地址（带有一个临时的 `authkey`）会被写进游戏目录下的缓存文件 `…_Data\webCaches\…\data_2`。软件**只读**这个文件，找出最新的那条链接，再用它去请求官方接口，每个卡池一页页取回。

**鸣潮**（库洛）：游戏会把记录页的链接写进 `Client.log`，这个日志是加密的，软件先解码，从中找到链接，再向官方接口发请求。鸣潮的记录没有唯一编号，软件按“时间 + 同一秒内第几条 + 卡池”给每条记录编号来去重，所以十连里同一秒出现的相同物品不会被误删。

记录保存在 `%LOCALAPPDATA%\GenshinWishLog\data`，每个游戏一个子文件夹、每个账号一个文件，以后每次更新都会合并新记录，不会重复。软件底部有「打开文件夹」。

不改游戏文件、不监听网络流量、不注入游戏进程，所以没有封号风险；软件没有任何对外监听的端口，记录只存在你自己电脑上，链接只会发给对应游戏的官方域名。

## 功能

- 四个游戏分开统计，每个游戏的卡池、品级（绝区零是 S/A/B）、货币（原石/星琼/菲林/星声）都按各自的规则显示
- 当前保底进度条、最高品级平均抽数、每个最高品级是第几抽出的（绿 / 黄 / 红按保底远近上色）
- 全部记录表格，可只看高品级
- 同一游戏多个账号可切换
- 导出 CSV（Excel 可直接打开中文）和 JSON，用系统的“另存为”对话框
- 自动找不到游戏时，在「高级」里点「浏览…」手动选游戏安装目录（选过一次会记住），或直接粘贴记录链接
- 「高级」里的「环境检测」：检查每个游戏的安装、缓存/日志、链接是否找到，结果里不含任何密钥，可以复制发给开发者排查问题

## 注意事项

- **官方只保留最近半年的记录**，所以隔一阵更新一次，更早的记录才会被攒在本地。**请备份 `%LOCALAPPDATA%\GenshinWishLog\data` 文件夹**，它是你唯一的完整存档（卸载软件不会删除它）。
- 记录链接相当于一把有时效的钥匙，别发给不信任的人。
- 「常驻」标记：在活动池里出了常驻角色，多半是歪了。但这些角色自己 UP 的复刻池里出了不算歪，从记录里无法区分，所以只做标记、不统计成"歪了几次"。目前只有原神和鸣潮有标记（崩铁、绝区零的常驻名单变动较多，暂不标）。名单在 `wishlog/games/mihoyo.py` 和 `wishlog/games/wuwa.py`，游戏更新后可以直接改。
- 保底抽数只用来画进度条和上色，若游戏规则调整，改对应游戏文件里的数字即可。
- 从 v0.3.0 起，原神的记录会自动从旧位置搬进 `data\genshin` 文件夹，旧文件备份在 `data\backup-before-multi-game`。
- 目前只支持国服（原神的 B 服链接也认）。终末地以后再做。

## 出问题时

先点「高级」→「环境检测」，把结果复制下来，能看出每一步卡在哪里。

| 提示 | 怎么办 |
| --- | --- |
| 软件启动时弹出“缺少 Microsoft Edge WebView2 运行时” | 按提示的网址下载安装 WebView2（Evergreen Bootstrapper），再打开软件 |
| 软件启动时弹出“出错了” | 详细信息保存在 `%LOCALAPPDATA%\GenshinWishLog\error.log`，把里面的内容发给开发者 |
| 没能自动找到这个游戏 | 先启动一次游戏；还不行就点「高级」→「浏览…」选游戏安装目录 |
| 没有找到游戏的网页缓存 / 缓存里没有祈愿链接 / 日志里没有唤取记录链接 | 在游戏里打开对应的记录页翻一翻，再更新 |
| 链接已经失效 | 同上：重新打开一次记录页再更新 |
| 读取缓存或日志失败（Permission denied） | 先完全退出游戏再试；如果游戏是以管理员身份运行的，软件也要用管理员身份运行 |
| 官方接口提示访问过于频繁 | 等几分钟再试 |

## 致谢和参考

没有这些开源项目公开的做法，就没有这个软件。本项目都是用 Python 按自己的结构重新实现的，没有搬用它们的代码：

- [Starward](https://github.com/Scighost/Starward)、[star-rail-warp-export](https://github.com/biuuu/star-rail-warp-export)、[genshin-wish-export](https://github.com/biuuu/genshin-wish-export)：米哈游游戏的缓存位置、接口地址、卡池编号，以及读缓存时需要的文件共享方式。
- [wuwa-gagha-tool](https://github.com/juliy819/wuwa-gagha-tool)（Apache-2.0）：鸣潮加密日志的解码方式、记录页链接的格式、请求体字段、13 个卡池的编号，以及按“同一秒内第几条”给记录编号来去重的思路。

## 开发

软件界面是一个本地网页（`wishlog/static/index.html`），由 [pywebview](https://pywebview.flowrl.com/) 放进系统原生窗口里显示，界面通过 `window.pywebview.api` 直接调用 Python 后台，没有 HTTP 服务。核心逻辑只用 Python 标准库。

```bash
pip install -r requirements.txt
python run.py                 # 打开软件窗口
python run.py --demo          # 用演示数据预览界面（不碰真实记录）
python -m unittest discover -s . -p "test_*.py" -t .   # 跑测试（假接口 + 假缓存/日志 + 假对话框）
python tools/make_icon.py     # 重新生成图标
```

打包由 GitHub Actions 完成（`.github/workflows/build-windows.yml`）：在真正的 Windows 上跑测试、打包、**真的打开软件做自检**、截图、生成安装包和免安装压缩包。给仓库打上和 `wishlog/__init__.py` 里版本号一致的标签（例如 `v0.3.0`），就会自动发布 Release。

加一个新游戏：在 `wishlog/games/` 里加一份配置（米哈游系的游戏只需要配置域名、路径、卡池编号），在 `wishlog/games/__init__.py` 里登记，界面和统计会自动适配。

目录结构：

- `wishlog/games/` 各游戏的定义：`base.py` 统一的卡池/品级/游戏定义，`mihoyo.py` 原神、崩铁、绝区零，`wuwa.py` 鸣潮
- `wishlog/locate.py` 找游戏目录、读缓存、取链接；`wishlog/client.py` 米哈游接口客户端；`wishlog/sync.py` 增量抓取
- `wishlog/job.py` 后台更新任务（单个游戏 / 更新全部）；`wishlog/store.py` 本地存储与去重、旧数据迁移
- `wishlog/stats.py` 保底与出金统计；`wishlog/api.py` 界面调用的全部后台方法；`wishlog/exporting.py` 导出
- `wishlog/app.py` 窗口外壳（创建窗口、WebView2 检查、自检）；`wishlog/paths.py` 数据目录；`wishlog/settings.py` 小设置
- `wishlog/static/index.html` 界面；`installer/GenshinWishLog.iss` 安装包脚本；`assets/` 图标；`tools/make_icon.py` 图标生成器
