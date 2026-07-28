# OpenList RAW Preview

[![CI](https://github.com/SSSimpleC/openlist-raw-preview/actions/workflows/ci.yml/badge.svg)](https://github.com/SSSimpleC/openlist-raw-preview/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

一个独立的、请求驱动的 RAW 照片网页预览 sidecar，通过 OpenList 的
`iframe_previews` 配置接入。它不是 OpenList 官方插件，也不修改 OpenList 主程序。

## 能做什么

- 在浏览器中预览常见相机 RAW：NEF、ARW、CR2、CR3、DNG、RAF、ORF、RW2、SRW 等。
- 优先使用 RAW 内嵌 JPEG，速度快且通常保留相机的方向和渲染结果。
- 没有足够大的内嵌预览时，使用 rawpy/LibRaw 的低成本半尺寸解码。
- 输出受限的 JPEG，不修改 RAW 原件。
- 按需解码并缓存，不扫描照片目录、不生成缩略图索引、不常驻处理照片。
- 本地文件可用只读映射直接读取；未配置本地映射时，通过带签名的 OpenList URL
  临时下载并在请求结束后删除。

## 项目边界

本项目是第三方集成：OpenList 负责文件列表、权限和签名 URL，本项目只负责把已
授权的 RAW 文件转换为浏览器可显示的 JPEG。项目与 OpenList Team 没有隶属关系，
也不保证 OpenList 未公开的内部 API 行为。

这是一个个人维护的独立项目，不是 OpenList 官方插件、官方扩展或官方推荐方案。
如果你在发行版、教程或产品中重新分发它，请保留本仓库的许可证、第三方声明和
本段非官方关系说明。

本项目原创代码使用 MIT License；依赖和 OpenList 各自适用其原有许可证。详见
[`NOTICE.md`](NOTICE.md)。

## 快速开始

需要一台能运行 Docker Compose 的主机。先复制并编辑环境文件：

```sh
cp .env.example .env
# 编辑 .env，至少设置 RAW_PREVIEW_OPENLIST_ORIGINS
docker compose --env-file .env up -d --build
```

检查服务：

```sh
curl http://127.0.0.1:5260/healthz
docker compose ps
```

默认只绑定 `127.0.0.1`。如果 OpenList 在另一台局域网设备上打开，需要把
`RAW_PREVIEW_BIND_ADDRESS` 改为服务器局域网地址或 `0.0.0.0`，并在主机防火墙中
限制 5260 只允许可信局域网访问。

## 接入 OpenList

将 `examples/openlist-iframe-previews.json` 中的服务器地址替换为实际预览服务地址，
再将映射加入 OpenList 的 `iframe_previews` 配置：

```json
{
  "3fr,arw,ari,bay,cap,cr2,cr3,crw,dcr,dng,erf,fff,iiq,k25,kdc,mef,mos,mrw,nef,nrw,orf,pef,raf,raw,rw2,rwl,sr2,srf,srw,x3f": {
    "RAW 预览": "http://SERVER_LAN_ADDRESS:5260/view?src=$e_url"
  }
}
```

`$e_url` 必须原样保留。浏览器打开 OpenList 后点击 RAW 文件，选择“RAW 预览”
即可。完整配置和本地只读映射见 [`docs/configuration.md`](docs/configuration.md)。

## 安全设计

- 默认不信任任何 OpenList origin，必须由部署者明确配置。
- 只接受允许 origin 下带签名的 OpenList `/p/...` 文件 URL。
- 校验路径并阻止 `..` 越界；本地存储应使用只读容器挂载。
- 远端下载有大小限制，临时文件在请求结束后删除。
- 容器丢弃 Linux capabilities，使用非 root 用户、只读根文件系统、临时 `/tmp`、
  进程数/内存/CPU 限制和日志轮转。
- 不要把预览端口、签名 URL 或照片原件直接暴露到公网。

签名 URL 相当于短期 bearer token，不要把包含 `sign=` 的完整 URL 写入公开日志或
提交到 Git。

## 性能与兼容性

默认单次只运行一个解码子进程，并将 OpenMP/BLAS 线程限制为 1。空闲时不会扫描
目录或解码文件。某些较新的相机型号可能需要更新 rawpy/LibRaw，某些 RAW 可能
没有可用内嵌预览且 LibRaw 不支持；此时服务会返回解码错误，而不会修改原件。

这是一种快速网页预览，不是完整 RAW 冲印流程，不保证与 Lightroom、Capture One
等软件的色彩、白平衡和镜头校正结果一致。

## 测试

```sh
python3 -m py_compile app/*.py tests/*.py
PYTHONPATH=. python3 -m unittest discover -s tests -v
docker compose --env-file .env.example config --quiet
```

## 贡献与发布

欢迎将本项目作为个人 GitHub 仓库发布。发布时请保留第三方项目说明、许可证和
“非 OpenList 官方项目”的声明；不要把它命名成官方插件或暗示 OpenList Team 背书。
若未来希望进入 OpenList 主项目，再另行按其贡献指南讨论文档示例或原生集成方案，
这不是本仓库发布的前置条件。

本仓库的初始版本在人工设计、审阅和测试的基础上使用了 AI 辅助开发。具体范围、
人工验证边界和后续贡献披露规则见 [`AI_USAGE.md`](AI_USAGE.md)。
