# Claude Code API - Simple & Working

# Python targets
install:
	uv sync
	uv add requests

test:
	uv run pytest tests/ -v

test-real:
	uv run python tests/test_real_api.py

start:
	uv run uvicorn claude_code_api.main:app --host 0.0.0.0 --port 8000 --reload --reload-exclude="*.db*" --reload-exclude="*.log"

start-prod:
	uv run uvicorn claude_code_api.main:app --host 0.0.0.0 --port 8000

clean:
	find . -name "*.pyc" -delete
	find . -name "__pycache__" -delete
	rm -rf build/ dist/ *.egg-info/
	rm -rf docker-build/

# Binary packaging targets
install-build-deps:
	uv add --dev pyinstaller

build-binary:
	@echo "Building standalone binary with PyInstaller..."
	uv run pyinstaller --onefile \
		--name claude-code-api \
		--add-data "claude_code_api:claude_code_api" \
		--hidden-import uvicorn \
		--hidden-import uvicorn.lifespan.on \
		--hidden-import uvicorn.lifespan.off \
		--hidden-import uvicorn.protocols.websockets.auto \
		--hidden-import uvicorn.protocols.http.auto \
		--hidden-import uvicorn.protocols.http.h11_impl \
		--hidden-import uvicorn.protocols.websockets.websockets_impl \
		--hidden-import uvicorn.loops.auto \
		--hidden-import uvicorn.loops.asyncio \
		--hidden-import fastapi \
		--hidden-import sqlalchemy.dialects.sqlite \
		--hidden-import aiosqlite \
		--hidden-import structlog \
		--hidden-import pydantic \
		--hidden-import pydantic_settings \
		claude_code_api/main.py
	@echo "Binary created in dist/claude-code-api"

package-release:
	@echo "Creating release package..."
	mkdir -p dist/release
	cp dist/claude-code-api dist/release/
	cp README.md dist/release/
	cp .env.example dist/release/
	chmod +x dist/release/claude-code-api
	cd dist && tar -czf claude-code-api-release.tar.gz release/
	@echo "Release package created: dist/claude-code-api-release.tar.gz"

kill:
	@if [ -z "$(PORT)" ]; then \
		echo "Error: PORT parameter is required. Usage: make kill PORT=8001"; \
	else \
		echo "Looking for processes on port $(PORT)..."; \
		if [ "$$(uname)" = "Darwin" ] || [ "$$(uname)" = "Linux" ]; then \
			PID=$$(lsof -iTCP:$(PORT) -sTCP:LISTEN -t); \
			if [ -n "$$PID" ]; then \
				echo "Found process(es) with PID(s): $$PID"; \
				kill -9 $$PID && echo "Process(es) killed successfully."; \
			else \
				echo "No process found listening on port $(PORT)."; \
			fi; \
		else \
			echo "This command is only supported on Unix-like systems (Linux/macOS)."; \
		fi; \
	fi

help:
	@echo "Claude Code API Commands:"
	@echo ""
	@echo "Python API (using uv):"
	@echo "  make install     - Install Python dependencies with uv"
	@echo "  make test        - Run Python unit tests with real Claude integration"
	@echo "  make test-real   - Run REAL end-to-end tests (curls actual API)"
	@echo "  make start       - Start Python API server (development with reload)"
	@echo "  make start-prod  - Start Python API server (production)"
	@echo ""
	@echo "Binary Packaging:"
	@echo "  make install-build-deps - Install PyInstaller with uv"
	@echo "  make build-binary       - Build standalone binary with PyInstaller"
	@echo "  make package-release    - Create complete release package"
	@echo ""
	@echo "General:"
	@echo "  make clean       - Clean up Python cache files and build artifacts"
	@echo "  make kill PORT=X - Kill process on specific port"
	@echo ""
	@echo "IMPORTANT: Both implementations are functionally equivalent!"
	@echo "Use Python or TypeScript - both provide the same OpenAI-compatible API."