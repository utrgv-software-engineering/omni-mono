# OmniAgents Current Issues

This document tracks verified architectural issues and missing features in the OmniAgents codebase based on code analysis.

## 🔴 Critical Issues

### 1. No Version Management

**Problem:**
- Version only exists in `pyproject.toml` (hardcoded as "0.1.3")
- No `__version__` attribute in the codebase
- Cannot programmatically access version information

**Code Evidence:**
```toml
# pyproject.toml
[project]
name = "omniagents"
version = "0.1.3"
```

**Impact:**
- Cannot display version in CLI/API responses
- Cannot track deployments programmatically
- Difficult to debug version-specific issues

**Solution:**
Create `omniagents/__version__.py`:
```python
__version__ = "0.1.3"
__version_info__ = (0, 1, 3)
```

### 2. Minimal Error Handling

**Problem:**
- Only transport-specific exceptions exist (`TransportError`, `TransportClosedError`)
- No comprehensive exception hierarchy
- No error middleware or structured error responses
- Errors can crash the application without proper handling

**Code Evidence:**
```python
# omniagents/rpc/transport/__init__.py
class TransportError(Exception):
    """Base exception for transport-related errors."""
    pass

class TransportClosedError(TransportError):
    """Raised when attempting to use a closed transport."""
    pass
```

**Impact:**
- Ungraceful failures in production
- Difficult to debug issues
- Poor user experience with cryptic error messages

**Solution:**
- Create comprehensive exception hierarchy in `omniagents/core/exceptions.py`
- Add error handling middleware
- Implement structured error responses

### 3. No Production Monitoring

**Problem:**
- No health check endpoints
- No metrics or observability
- No way to monitor application status
- Basic logging exists but no structured logging

**Code Evidence:**
```python
# omniagents/backends/server/app.py - No health endpoints
def build_app(...) -> FastAPI:
    app = FastAPI()
    # Only WebSocket endpoint registered
    endpoint.register_route(app, "/ws")
    return app
```

**Impact:**
- Cannot monitor application health in production
- No visibility into performance issues
- Difficult to debug production problems

**Solution:**
- Add `/health` endpoint
- Add basic metrics collection
- Implement structured logging with context

### 4. Limited Security Features

**Problem:**
- Basic token authentication exists (query parameter only)
- No rate limiting implementation
- No API key management system
- Token passed as query parameter (less secure than headers)

**Code Evidence:**
```python
# omniagents/backends/server/app.py
async def _verify_token(websocket: WebSocket):
    if required_token:
        if websocket.query_params.get("token") != required_token:
            await websocket.close(code=4403)
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid token")
```

**Impact:**
- Vulnerable to brute force attacks
- Tokens visible in logs/URLs
- No way to manage multiple API keys

**Solution:**
- Add rate limiting middleware
- Support bearer token authentication
- Implement proper API key management

## ⚠️ Architecture Concerns

### 5. Test Organization

**Problem:**
- Tests were previously flat in `/tests` directory
- Now reorganized but some tests still fail due to code changes
- No clear testing strategy documented

**Status:**
- ✅ Test structure reorganized into unit/integration/e2e
- ❌ 10 tests failing (not due to reorganization)

### 6. Limited Documentation

**Problem:**
- Basic README exists
- No API documentation
- No deployment guide
- No security best practices documented

**Files:**
- `/README.md` - Basic usage
- `/CLAUDE.md` - AI assistant instructions
- `/docs/ISSUES.md` - This file
- No comprehensive documentation

## Priority Ranking

1. **Version Management** - Quick fix, high impact
2. **Error Handling** - Critical for production
3. **Health Checks** - Essential for operations
4. **Rate Limiting** - Important for security
5. **Documentation** - Needed for adoption

## Notes

- The codebase has a clean architecture with good separation of concerns
- Database layer is already unified (no duplication found)
- Basic abstractions and protocols are well-designed
- Main gaps are production-readiness features