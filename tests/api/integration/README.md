# 集成测试目录

本目录包含对 P0 修复项的端到端集成测试用例。

## 测试覆盖范围

| 测试文件 | P0 任务 | 覆盖功能 |
|----------|---------|----------|
| `test_image_extraction.py` | BP-001 | 图片股票提取 API（增强版） |
| `test_permission_auth.py` | BP-003 | 权限保护与认证检查 |
| `test_sse_integration.py` | BP-002 | SSE 实时流式推送 |

## 运行方法

### 安装依赖
```bash
cd /mnt/daily_stock_analysis
pip install pytest pytest-mock httpx
```

### 运行所有集成测试
```bash
pytest tests/api/integration/ -v --cov=src --cov-report=html
```

### 运行单个测试文件
```bash
pytest tests/api/integration/test_image_extraction.py -v
pytest tests/api/integration/test_permission_auth.py -v
pytest tests/api/integration/test_sse_integration.py -v
```

### 跳过网络相关测试（如果配置了外部服务）
```bash
pytest tests/api/integration/ -m "not network" -v
```

## 测试说明

### test_image_extraction.py
需要 mock `src.services.image_stock_extractor.extract_stock_codes_from_image` 函数以模拟 Vision LLM 调用。测试场景包括：
- 正常成功路径
- 文件过大 (413)
- 无效 MIME 类型 (400)
- 缺少文件 (400)
- Vision API 不可用 (503)
- 空结果返回 (200 + empty list)
- 其他异常 (500)

### test_permission_auth.py
需要 mock会话验证逻辑。测试场景包括：
- PUT /config 需要管理员权限
- POST /config/import-env-backup 需要管理员权限  
- /auth/settings 不再豁免，需要认证
- 403 响应格式包含详细错误信息

### test_sse_integration.py
测试 SSE 端点的基础连通性。由于实际流式测试需要 async test client，当前实现侧重：
- Content-Type 头验证
- 认证检查通过 middleware 间接验证
- 事件结构契约测试
- Heartbeat 间隔逻辑验证（代码级）

## 注意事项

1. 运行前确保数据库和后端服务已准备就绪（对于需要真实连接的测试）
2. 某些测试需要在特定环境变量下运行（如 ADMIN_AUTH_ENABLED=true）
3. 建议先运行单元测试确保依赖模块可导入
