# 第三方项目与许可说明

本仓库是一个独立的第三方 OpenList Iframe Preview sidecar，不是 OpenList
官方插件，也不代表 OpenList Team。仓库没有复制或修改 OpenList 主程序源代码。

OpenList 主项目采用其仓库声明的 AGPL-3.0 许可证；本项目只调用其文档化的预览
配置，不把 OpenList 源码打包进本项目。将本项目与 OpenList 或其他软件组合分发时，
请分别遵守各组件的许可证要求。

- OpenList：本项目通过其文档化的 Iframe Preview 配置传入 `$e_url`；OpenList
  本身的代码、名称和许可证以其官方仓库为准：
  <https://github.com/OpenListTeam/OpenList>
- OpenList 预览配置：<https://doc.oplist.org/configuration/preview>
- rawpy：Python 的 LibRaw 包装器，依赖及其许可证以项目仓库和发布包为准：
  <https://github.com/letmaik/rawpy>
- LibRaw：RAW 解码库，许可证和相机支持以其官方项目为准：
  <https://www.libraw.org/>
- Pillow、NumPy：运行时依赖，许可证以各自项目和安装包为准。

本项目原创代码使用 MIT License。使用者需要同时遵守运行时依赖、OpenList
以及所接入存储服务各自的许可证和使用条款。
