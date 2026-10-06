# 原神抽卡记录（国服 · Windows 桌面软件）

一个自带窗口的桌面软件：读取你电脑上游戏留下的缓存，取回官方的祈愿记录，在本机统计保底、出金，并长期保存。

## 安装和使用

到 [Releases](https://github.com/xqf946/genshin-wish-log/releases) 页面下载：

- **`GenshinWishLog-Setup-x.y.z.exe`**：安装包。双击安装，会在开始菜单和桌面创建快捷方式，可以正常卸载。不需要管理员权限。
- **`GenshinWishLog-x.y.z-portable.zip`**：免安装版。解压到一个固定的文件夹，双击里面的 `GenshinWishLog.exe` 即可。

使用：

1. 打开原神，进入「祈愿」→ 左下角「历史记录」，随便点开一个卡池翻一翻（**每次更新前都要做**，链接约 24 小时失效）。
2. 打开本软件，点右上角「更新记录」。

首次运行时 Windows 可能提示“Windows 已保护你的电脑”（SmartScreen）：这是因为程序没有付费的代码签名，点「更多信息」→「仍要运行」即可。部分杀毒软件也可能误报，需要的话把它加入信任。

系统要求：Windows 10 / 11（64 位）。窗口内容由 Microsoft Edge WebView2 运行时绘制，Win11 和绝大多数 Win10 已自带；如果没有，软件启动时会告诉你去哪里下载。

## 它是怎么工作的

1. 你在游戏里打开「祈愿 → 历史记录」时，游戏内置的网页会请求官方接口，这个请求地址（带有一个临时的 `authkey`）会被写进游戏目录下的缓存文件 `YuanShen_Data\webCaches\...\data_2`。
2. 本软件**只读**这个文件，找出最新的那条链接，再用它去请求官方的祈愿记录接口，每个卡池一页页取回。
3. 记录保存在 `%LOCALAPPDATA%\GenshinWishLog\data`（每个 UID 一个文件），以后每次更新都会合并新记录，不会重复。软件底部有「打开文件夹」。

不改游戏文件、不监听网络流量、不注入游戏进程，所以没有封号风险；软件没有任何对外监听的端口，记录只存在你自己电脑上，链接只会发给 `*.mihoyo.com`。

## 功能

- 角色 / 武器 / 集录 / 常驻 / 新手 五个卡池分开统计
- 当前保底进度条、五星平均抽数、每个五星是第几抽出的（绿 / 黄 / 红按保底远近上色）
- 全部记录表格，可只看四星以上或五星
- 多个 UID 可切换
- 导出 CSV（Excel 可直接打开中文）和 JSON，用系统的“另存为”对话框
- 自动找不到游戏目录时，可以点「浏览…」手动选，或直接粘贴祈愿链接

## 注意事项

- **官方只保留最近半年的记录**，所以隔一阵更新一次，更早的记录才会被攒在本地。**请备份 `%LOCALAPPDATA%\GenshinWishLog\data` 文件夹**，它是你唯一的完整存档（卸载软件不会删除它）。
- 祈愿链接相当于一把 24 小时有效的钥匙，别发给不信任的人。
- 「常驻」标记：活动池里出了常驻五星角色，多半是歪了。但这些角色自己 UP 的复刻池里出了不算歪，从记录里无法区分，所以只做标记、不统计成"歪了几次"。常驻角色名单在 `wishlog/pools.py`，游戏更新后可以直接改。
- 保底抽数（角色 90、武器 80、集录 90）只用来画进度条和上色，若游戏规则调整，改 `wishlog/pools.py` 里的数字即可。
- 目前只支持原神国服（官服 / B服）。

## 出问题时

| 提示 | 怎么办 |
| --- | --- |
| 软件启动时弹出“缺少 Microsoft Edge WebView2 运行时” | 按提示的网址下载安装 WebView2（Evergreen Bootstrapper），再打开软件 |
| 软件启动时弹出“出错了” | 详细信息保存在 `%LOCALAPPDATA%\GenshinWishLog\error.log`，把里面的内容发给开发者 |
| 没能自动找到游戏目录 | 先启动一次游戏；还不行就点「高级」→「浏览…」选游戏安装目录（里面有 `YuanShen.exe` 的文件夹） |
| 没有找到游戏的网页缓存 / 缓存里没有祈愿链接 | 在游戏里打开「祈愿 → 历史记录」翻一翻，再更新 |
| 祈愿链接已经失效 | 同上：重新打开一次历史记录页面再更新 |
| 官方接口提示访问过于频繁 | 等几分钟再试 |

## 开发

软件界面是一个本地网页（`wishlog/static/index.html`），由 [pywebview](https://pywebview.flowrl.com/) 放进系统原生窗口里显示，界面通过 `window.pywebview.api` 直接调用 Python 后台，没有 HTTP 服务。核心逻辑只用 Python 标准库。

```bash
pip install -r requirements.txt
python run.py                 # 打开软件窗口
python run.py --demo          # 用演示数据预览界面（不碰真实记录）
python -m unittest discover -s . -p "test_*.py" -t .   # 跑测试（假接口 + 假缓存文件 + 假对话框）
python tools/make_icon.py     # 重新生成图标
```

打包由 GitHub Actions 完成（`.github/workflows/build-windows.yml`）：在真正的 Windows 上跑测试、打包、**真的打开软件做自检**、截图、生成安装包和免安装压缩包。给仓库打上和 `wishlog/__init__.py` 里版本号一致的标签（例如 `v0.2.0`），就会自动发布 Release。

目录结构：

- `wishlog/locate.py` 找游戏目录、读缓存、取链接
- `wishlog/client.py` 官方接口客户端（域名白名单、翻页、重试、过期判断）
- `wishlog/sync.py` 增量抓取，按卡池提交；`wishlog/job.py` 后台更新任务
- `wishlog/store.py` 本地存储与去重；`wishlog/stats.py` 保底与出金统计
- `wishlog/api.py` 界面调用的全部后台方法；`wishlog/exporting.py` 导出
- `wishlog/app.py` 窗口外壳（创建窗口、WebView2 检查、自检）；`wishlog/paths.py` 数据目录
- `wishlog/static/index.html` 界面
- `installer/GenshinWishLog.iss` 安装包脚本；`assets/` 图标；`tools/make_icon.py` 图标生成器
