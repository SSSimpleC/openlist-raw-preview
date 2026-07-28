## 变更内容

<!-- 简述改了什么、为什么改，以及对部署者的影响。 -->

## 安全与兼容性

- [ ] 没有提交凭据、签名 URL、真实内网地址、磁盘 UUID、个人媒体或测试原件
- [ ] 保持 origin、签名 URL、路径校验和只读挂载边界
- [ ] 说明了新增依赖、配置项和资源影响

## 验证

- [ ] `python3 -m py_compile app/*.py tests/*.py`
- [ ] `PYTHONPATH=. python3 -m unittest discover -s tests -v`
- [ ] `docker compose --env-file .env.example config --quiet`

## AI 辅助

<!-- 如果 AI 对代码、测试、文档或设计产生了实质性影响，请说明范围和人工验证方式。 -->
