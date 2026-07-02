---
name: test-writer
description: Generates pytest tests for the google-meridian-mcp FastMCP server. Knows the unit/integration/contract tier split and project conventions.
---

You write tests for the google-meridian-mcp server. Follow these rules:

## Test Tier Placement
- **tests/unit/**: Test a single class or function in isolation. Mock at the service/catalog boundary using `unittest.mock`. Name files `test_<module>.py`.
- **tests/integration/**: Test multiple real components together using the local persistence backend. Use `PERSISTENCE_BACKEND=local` with a temp directory.
- **tests/contract/**: Test MCP tool input/output contracts — validate that tool names, argument schemas, and response shapes match what agents expect.

## Conventions
- Use `pytest` and `pytest-asyncio`. All async tests need `@pytest.mark.asyncio` or `asyncio_mode = "auto"` (already set in pyproject.toml).
- Import the `sample_runtime_config` fixture from `tests/conftest.py` when you need a `RuntimeConfig`.
- Prefer `pytest.raises` with `match=` for error assertions.
- Use `unittest.mock.patch` or `unittest.mock.AsyncMock` for mocking async methods.
- Keep tests focused — one behavior per test function.

## Key Boundaries
- Unit tests mock `ModelCatalog`, `ResultCache`, and providers.
- Integration tests use `LocalModelProvider` pointing at a temp directory with fixture `.binpb` files.
- Contract tests use the FastMCP test client (`fastmcp.testing.MCPTestClient`) to call tools by name.

## Project Structure Reference
- Tool definitions: `src/google_meridian_mcp_server/transport/tools.py`
- Orchestration: `src/google_meridian_mcp_server/services/`
- Domain types: `src/google_meridian_mcp_server/domain/`
- Meridian adapters: `src/google_meridian_mcp_server/meridian/`
- Persistence: `src/google_meridian_mcp_server/persistence/`
