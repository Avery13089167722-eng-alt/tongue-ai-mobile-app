# 舌征智析手机端（Python / Kivy）

这是一个可打包为 Android APK 的舌象分析应用：
- 手机端拍照/上传舌象图
- 调用云端大模型做中医分析
- 实时显示分析状态与结果
- 历史记录存储在手机本地 SQLite

## 1. 功能概览

- 仿 AI 问答 App 的深色风格界面
- Android 真机拍照 + 图片选择与预览
- 分析中进度条反馈
- 失败自动重试 + 超时友好提示
- 结果文本展示
- 本地历史记录（最近 30 条）
- 预留 API Token 鉴权

## 2. 项目结构

- `main.py`：主应用和交互逻辑
- `ui.kv`：前端界面
- `api_client.py`：云端大模型调用封装
- `storage.py`：SQLite 本地存储
- `app_config.json`：接口配置
- `requirements.txt`：依赖

## 3. API 对接（已对接你的服务器）

客户端请求（POST）：
- URL: `{api_base_url}{api_path}`，默认 `http://8.160.184.31:8001/v1/tongue-analyze`
- Header:
  - `Authorization: Bearer <token>`（可选）
  - `Content-Type` 由 `multipart/form-data` 自动生成
- Body（multipart/form-data）:

- `file`：图片二进制（必填）
- `question`：补充提问（可选）
- `max_new_tokens`：最大输出 token（可选，默认 512）

服务端返回（实际）：

```json
{
  "answer": "详细分析文本...",
  "filename": "tongue_20260319_xxx.jpg"
}
```

## 4. 本地运行

```bash
pip install -r requirements.txt
python main.py
```

## 5. 打包 APK（推荐 Linux/WSL 环境）

由于 Buildozer 对 Linux 支持最佳，建议在 WSL Ubuntu 或 Linux 主机打包：

1) 安装系统依赖
```bash
sudo apt update
sudo apt install -y python3-pip git zip unzip openjdk-17-jdk autoconf libtool pkg-config zlib1g-dev libncurses5-dev libncursesw5-dev libtinfo5 cmake libffi-dev libssl-dev
pip install --upgrade pip
pip install buildozer cython
```

2) 项目已提供 `buildozer.spec`，按需修改：
- `package.domain`（建议换成你的域名反写）
- `android.api/minapi`（按你的目标机型）

3) 生成 APK
```bash
buildozer -v android debug
```

生成后 APK 在 `bin/` 目录，可直接安装到其他 Android 设备。

## 6. 可移植性说明

- 应用数据存储在设备本地数据库 `tongue_records.db`
- 安装包为标准 APK，可通过 ADB、文件传输、企业分发等方式安装
- 只要能访问你的云端 IP 和端口，即可在其他手机使用

## 7. 下一步建议

- 增加分段结果卡片（体质、寒热、建议）
- 增加 SSL（HTTPS）和接口签名

## 8. 跨设备字体一致性（已启用）

- 项目内置字体：`assets/fonts/NotoSansSC-Regular.ttc`
- 应用启动时优先加载该字体并覆盖 Kivy/KivyMD 默认字体
- `buildozer.spec` 已加入 `ttf/otf/ttc`，打包时会包含字体文件
- 这样 APK 在不同手机上也能稳定显示中文，不依赖系统字体

