# 贡献指南

欢迎提交问题和改进。提交前请：

1. 不要加入 RAW 原件、缩略图、签名 URL、凭据、内网地址或真实磁盘路径。
2. 运行 `python3 -m py_compile app/*.py tests/*.py`。
3. 运行 `PYTHONPATH=. python3 -m unittest discover -s tests -v`。
4. 如果修改容器配置，运行 `docker compose --env-file .env.example config --quiet`。

代码应保持请求驱动、无目录扫描、单次最多一个解码任务，并避免引入常驻索引或
后台扫描器。大功能应先在 issue 中说明用途、资源影响和安全边界。
