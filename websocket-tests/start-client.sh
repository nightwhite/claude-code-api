#!/bin/bash

# WebSocket 测试客户端启动脚本

echo "🚀 启动 WebSocket 文件监控测试客户端"
echo "=================================="

# 检查端口是否被占用
PORT=3000
if lsof -Pi :$PORT -sTCP:LISTEN -t >/dev/null ; then
    echo "⚠️  端口 $PORT 已被占用，尝试使用端口 3001"
    PORT=3001
fi

if lsof -Pi :$PORT -sTCP:LISTEN -t >/dev/null ; then
    echo "⚠️  端口 $PORT 也被占用，尝试使用端口 3002"
    PORT=3002
fi

echo "📡 在端口 $PORT 启动 HTTP 服务器..."
echo "🌐 客户端地址: http://0.0.0.0:$PORT"
echo "🔌 API 服务器: http://0.0.0.0:8080"
echo ""
echo "💡 使用说明:"
echo "   1. 确保 API 服务器在端口 8080 运行"
echo "   2. 在浏览器中打开客户端地址"
echo "   3. 点击'连接'按钮连接到 WebSocket"
echo "   4. 配置监控路径并开始监控"
echo ""
echo "⏹️  按 Ctrl+C 停止服务器"
echo "=================================="

# 启动 Python HTTP 服务器
python3 -m http.server $PORT
