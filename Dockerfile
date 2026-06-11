# LDLAWQ —— Python 标准库实现，零第三方依赖
FROM python:3.12-slim

WORKDIR /app
COPY . .

# 构建期重建知识库（knowledge.db 可由 seed 完全复现）
RUN python3 src/build_knowledge.py

# Zeabur/各平台通过 PORT 环境变量注入端口，server.py 自动读取
EXPOSE 8400
CMD ["python3", "src/server.py"]
