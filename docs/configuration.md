# 配置说明

## 1. OpenList origin

在 `.env` 中设置浏览器实际打开的 OpenList 地址，多个地址用逗号分隔：

```dotenv
RAW_PREVIEW_OPENLIST_ORIGINS=http://openlist.example.test:5244,http://192.0.2.10:5244
```

协议、主机和端口必须完全匹配。服务默认不信任任何 origin。

## 2. 端口绑定

如果 OpenList 和预览服务运行在同一台服务器，且浏览器也在服务器上，可以保留
`127.0.0.1`。如果浏览器运行在另一台局域网设备上，应将
`RAW_PREVIEW_BIND_ADDRESS` 改成服务器局域网 IP 或 `0.0.0.0`，并只在主机防火墙
放行可信局域网访问。

不要直接把 5260 转发到互联网。若要从外网访问，应先建立经过认证的私有网络或
反向代理边界，并重新评估签名 URL、CSP 和日志策略。

## 3. 本地只读直读（可选）

不配置本地映射时，sidecar 会使用带签名的 OpenList URL 按需下载 RAW 到临时目录，
解码后删除临时文件。配置本地映射可以避免同一台服务器上的重复网络读取：

```dotenv
RAW_PREVIEW_SOURCE_MOUNTS={"Photos":"/sources/Photos"}
```

同时在 Compose override 中添加与之对应的只读挂载：

```yaml
services:
  raw-preview:
    volumes:
      - type: bind
        source: /absolute/path/on/server
        target: /sources/Photos
        read_only: true
        bind:
          propagation: rslave
```

`Photos` 必须与 OpenList URL `/p/Photos/...` 的第一级存储名一致。不要把数据盘以
读写方式挂载给预览容器。

## 4. OpenList iframe 配置

将 `examples/openlist-iframe-previews.json` 中的
`SERVER_LAN_ADDRESS` 替换为预览服务地址，然后把映射加入 OpenList 的
`iframe_previews` 配置。关键部分是：

```text
http://SERVER_LAN_ADDRESS:5260/view?src=$e_url
```

`$e_url` 必须原样保留；它包含 OpenList 生成的签名文件 URL。保存配置后，从
OpenList 点击 RAW 文件并选择对应的 iframe 预览项即可。

## 5. 资源参数

- `RAW_PREVIEW_MAX_EDGE`：输出 JPEG 长边，默认 2048。
- `RAW_PREVIEW_CACHE_MAX_BYTES`：JPEG 缓存上限，默认 5 GiB。
- `RAW_PREVIEW_MAX_REMOTE_BYTES`：远端 RAW 临时下载上限，默认 512 MiB。
- `RAW_PREVIEW_DECODE_TIMEOUT`：单个解码任务超时，默认 120 秒。
- `RAW_PREVIEW_METADATA_TIMEOUT`：单个 EXIF 读取任务超时，默认 30 秒。

## 6. EXIF 信息

预览页会在图片下方按需读取并显示一组受限的基础 EXIF 信息：相机、镜头、拍摄
时间、尺寸、快门、光圈、ISO、焦距和 GPS（若文件包含这些字段）。ExifRead 只读
源文件，结果以 JSON sidecar 缓存在服务的数据目录；不会修改 RAW，也不会扫描目录
或预先生成索引。元数据读取和 RAW 解码共用单任务锁，便于低功耗服务器控制并发。

GPS 属于敏感信息。若预览服务可能被不可信用户访问，建议不要把它暴露到公网；本
项目默认只允许明确配置的 OpenList origin，并且不把签名 URL 写入日志。

服务不会扫描照片目录或生成缩略图索引。空闲时只保留 HTTP 进程和 Python 运行时；
一次只允许一个解码任务，适合低功耗或单核服务器。
