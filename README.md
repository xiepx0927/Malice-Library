# 📚 Malice-Library

小说检索 / 在线阅读 / 双站图集（CosersLove + 爱妹子）/ 本地压缩包图集管理的自托管网页应用。

- **多站点小说检索**：模糊 / 标题 / 作者 / 多标签检索，分类浏览，在线阅读，单章或整本 TXT 下载
- **图集双源**：CosersLove + 爱妹子(LC)，首页画廊、关键词检索、标签/分类分页浏览，单击「全图」看全部大图
- **存到本地图书馆**：一键将整包图片打包压缩存入 `archives/` 并标记作者
- **本地压缩包管理**：zip / 7z / rar 图集网格浏览、灯箱大图、小说直读、视频播放，支持作者标记与等级（1-5★）

## 📥 下载与安装（Windows，首选支持平台）

在 GitHub Releases 下载 **完整包** `Malice-Library-{版本}.zip`：

1. 解压到任意文件夹（建议本地磁盘，避免 OneDrive/云同步盘）。
2. 双击 **`启动.bat`**。
   - 首次运行自动：创建虚拟环境 → 安装依赖 → 下载 Playwright Chromium（约 150MB，仅图集自动验证需要）。
   - 完成后自动弹开浏览器进入 `http://127.0.0.1:5000`。
3. 以后每次双击 `启动.bat` 即启动。停止服务：关闭黑色命令行窗口。

> 系统要求：Windows 10/11（64 位）+ Python 3.10+（安装时勾选 "Add python.exe to PATH"）。

### 手动安装（可选）

```bash
pip install flask requests beautifulsoup4 lxml py7zr Pillow
pip install playwright && python3 -m playwright install chromium
python app.py
```

> rar 支持需要额外安装 `unrar`（zip / 7z 无需额外依赖）。

## 版本与更新

- 版本号显示在页面左上角 Logo 旁（如 `v1.0.1`）。
- 点击顶栏 **「↻」** 按钮**手动**检查 GitHub 最新版本（程序不会自动联网检查）。
- 程序内置默认仓库 `xiepx0927/Malice-Library`：从压缩包解压运行、没有克隆 git 仓库也能正常检查更新。
- 每个版本在 GitHub Releases 同时发布两个包：
  - **完整包** `Malice-Library-{版本}.zip` —— 全部源码，适合**中途新用户**从任意版本直接升级
  - **增量包** `Malice-Library-update-{上一版本}-{版本}.zip` —— 仅本次变更文件 + `UPDATE.md` 变更说明，适合老用户一版一步平滑升级（更小、更快）
- 检查更新时程序会按你的本地版本自动推荐：落后一版用增量包，跳版用完整包。

### 一键更新

在检查更新弹窗中选择「一键更新」，程序自动完成：**下载 → 校验(SHA256) → 备份到 `backup/` → 替换 `app.py` / 前端模板 → 语法校验**，失败自动回滚。

### 图库安全（重要）

更新**绝不会**覆盖或删除以下内容：

- `archives/` —— 你的本地图库
- `cos_cookie.json` —— 你的站点 cookie
- `archives/.meta.json` —— 你的图集元数据（作者 / 等级）

更新器只允许写入源码白名单文件（`app.py`、`templates/`、`packaging/`、`start.sh`、`requirements.txt`、`README.md`、`build_release.py`），其余路径一律拒绝。

## 手动升级（不起用一键更新时）

1. 去 GitHub Releases 下载 **完整包**（或适应当前版本的**增量包**）。
2. 完整包：解压全部文件覆盖到程序目录（保留 `archives/`，结构见下）。
3. 增量包：按包内 `UPDATE.md` 清单，把 `files/` 下的文件按原路径覆盖；`UPDATE.md` 标注为删除的文件需手动删除（此类文件本包不含，见清单）。
4. 重启程序。

## 图集与站点说明

- 图集页面支持 **CosersLove** 与 **爱妹子** 双数据源切换（工具栏下拉）。
- 首次进入「图集」会自动通过原站 Cloudflare 验证获取 `cf_clearance` 并存入 `cos_cookie.json`（用无头 Chromium 自动完成，无需手动复制）。
- 标签分页可直接浏览 **全量** 标签图集（如 Cosplay、黑丝、JK制服…）；搜索结果精确命中标签名时会自动改用标签全量结果。
- cookie / 图库均为**本地文件**，构建与更新包均不包含任何用户数据。

### 配置

| 环境变量 | 作用 | 默认 |
| ---- | ---- | ---- |
| `ALS_PORT` | 服务端口 | 5000 |
| `ALS_HOST` | 监听地址（`0.0.0.0` 允许局域网访问） | `0.0.0.0` |

## 文件结构

```
Malice-Library/
├─ app.py               Flask 后端（API + 爬虫 + 本地压缩包 + 更新检查）
├─ build_release.py     发布打包脚本（完整包 + 增量包）
├─ templates/index.html 前端界面
├─ packaging/           启动.bat / 使用说明.txt / requirements.txt 源文件
├─ 启动.bat              Windows 一键启动（完整包根目录内置）
├─ 使用说明.txt          Windows 使用说明
├─ archives/            本地图库（自动生成；不入库）
├─ cos_cookie.json      站点 cookie（首次图集访问自动生成；不入库）
└─ .gitignore           排除 archives / cookie / 日志 / 构建产物
```

## 安全与隐私

- 仓库不包含任何 cookie、图包或个人信息。
- 本应用仅用于技术学习与自托管；请尊重上游站点版权与访问频率，合法合规使用。