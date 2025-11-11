# The OmniAgents Guide to Testing

## 1.0 Core Philosophy [CORE-PHIL]
The OmniAgents testing approach emphasizes testing through agent execution interfaces over isolated units, favoring simplicity and business value over comprehensive coverage. The majority of testing happens through RPC/agent integration tests, with selective unit tests for complex logic and focused E2E tests for critical workflows.

## 2.0 General Principles [GEN-PRIN]

### 2.1 Test Through Agent Execution When Possible [PRIN-EXEC]
- 2.1.1 RPC/Agent integration tests are your primary testing layer
- 2.1.2 Use real agent instances and actual tool execution
- 2.1.3 Avoid mocking the OpenAI agents library
- 2.1.4 Test the application as users (CLI/GUI/API clients) will experience it

### 2.2 Focus on Business Value [PRIN-VALUE]
- 2.2.1 Test agent behavior that matters to users
- 2.2.2 Skip testing OpenAI library internals and Python defaults
- 2.2.3 Don't aim for 100% test coverage
- 2.2.4 Write tests that provide confidence in agent orchestration

### 2.3 Keep Tests Simple [PRIN-SIMPLE]
- 2.3.1 Avoid complex test fixtures and mocking hierarchies
- 2.3.2 Use real SQLite sessions and actual tool execution
- 2.3.3 Keep setup minimal - create agents from simple configs
- 2.3.4 Make test intentions clear through naming

### 2.4 Tests Should Discover Bugs, Not Lock Them In [PRIN-INTENT]
- 2.4.1 **Test intended behavior, not current behavior** - Before writing assertions, understand what the code *should* do
- 2.4.2 **Question failures** - When a test fails, investigate whether it's:
  - A bug in the implementation (code needs fixing)
  - A bug in the test (test assumptions are wrong)
  - A combination of both
- 2.4.3 **Don't make tests pass blindly** - Resist the urge to change assertions just to make tests green
- 2.4.4 **Consult documentation and design intent** - Read docstrings, comments, and related code to understand intended behavior
- 2.4.5 **Use tests to probe edge cases** - Tests should explore boundary conditions and expose bugs
- 2.4.6 **Document assumptions** - When test behavior is ambiguous, document why you chose specific assertions

**Example of the wrong approach:**
```python
# BAD: Just testing what the code currently does
def test_score_calculation():
    result = calculate_score([0, 0, 1])  # Returns 0.5
    assert result == 0.5  # ❌ Locks in potentially wrong behavior
```

**Example of the right approach:**
```python
# GOOD: Testing intended behavior with reasoning
def test_score_calculation_fraction_mode():
    """
    Score calculation in 'fraction' mode should compute:
    - Pass rate = (number of passing measures) / (total measures)
    - For [fail, fail, pass]: 1/3 ≈ 0.333

    If this test fails, investigate:
    1. Is the implementation using the wrong formula?
    2. Is 'fraction' mode supposed to work differently?
    3. Check docstrings and evaluation spec
    """
    result = calculate_score([0, 0, 1], mode="fraction")
    assert result == pytest.approx(0.333, abs=0.01)  # ✓ Tests intent
```

## 3.0 Agent Integration Tests (Primary Testing Layer) [AGENT-TEST]

### 3.1 Purpose [AGENT-PURPOSE]
Agent integration tests verify behavior through the RPC protocol or direct agent execution, testing the full stack from configuration to tool execution to response streaming.

### 3.2 Key Aspects to Test [AGENT-ASPECTS]

#### 3.2.1 Agent Configuration & Initialization [AGENT-INIT]
- 3.2.1.1 Loading from YAML config
- 3.2.1.2 Tool discovery and registration
- 3.2.1.3 Instruction template rendering
- 3.2.1.4 MCP server initialization
- 3.2.1.5 Safe agent vs standard agent mode

#### 3.2.2 Agent Execution [AGENT-EXEC]
- 3.2.2.1 Message sending and streaming responses
- 3.2.2.2 Tool calling and result handling
- 3.2.2.3 Context variable injection
- 3.2.2.4 Session history persistence
- 3.2.2.5 Error handling and retries

#### 3.2.3 Tool Approval Flow [AGENT-APPROVAL]
- 3.2.3.1 Approval requests sent correctly
- 3.2.3.2 Approval/rejection handling
- 3.2.3.3 Rich preview rendering
- 3.2.3.4 Metadata propagation

#### 3.2.4 RPC Protocol [AGENT-RPC]
- 3.2.4.1 Event streaming (tool_called, tool_result, message_output)
- 3.2.4.2 Client function invocation (ui.request_tool_approval, ui.add_artifact)
- 3.2.4.3 Server function calls
- 3.2.4.4 WebSocket resilience and reconnection
- 3.2.4.5 Session lifecycle management

### 3.3 Best Practices [AGENT-BEST]
- 3.3.1 Use the RPC protocol or LocalAgent wrapper
- 3.3.2 Test both successful tool execution and failures
- 3.3.3 Verify session database state changes
- 3.3.4 Check event emission correctness
- 3.3.5 Test with real tool implementations when possible
- 3.3.6 Mock only external APIs (OpenAI, web services)
- 3.3.7 Use minimal YAML configs for test agents

**Example:**
```python
def test_agent_executes_tool_and_returns_result():
    # Minimal config
    config = AgentConfig(
        name="test-agent",
        instructions="You help with math",
        tools=["calculator"]
    )

    # Test through RPC/agent interface
    agent = create_agent(config)
    response = agent.send_message("What is 2+2?")

    # Verify behavior
    assert "4" in response.content
    assert_tool_called(response, "calculator", {"expression": "2+2"})
    assert_session_saved(agent.session_id)
```

## 4.0 Unit Tests (Core Logic) [UNIT-TEST]

### 4.1 When to Write Unit Tests [UNIT-WHEN]
Only write unit tests for:
- 4.1.1 Complex config parsing/validation logic
- 4.1.2 Evaluation metrics and scoring algorithms
- 4.1.3 Tool discovery and registration logic
- 4.1.4 Jinja template rendering with edge cases
- 4.1.5 RPC event serialization/deserialization
- 4.1.6 Session storage and retrieval logic

### 4.2 What to Skip [UNIT-SKIP]
- 4.2.1 Simple data classes (AgentConfig, ToolMetadata)
- 4.2.2 Basic Python properties and accessors
- 4.2.3 OpenAI agents library internals
- 4.2.4 Trivial wrapper functions
- 4.2.5 Backend UI rendering (test through integration)

### 4.3 Best Practices [UNIT-BEST]
- 4.3.1 Focus on parsing, transformation, and calculation logic
- 4.3.2 Test edge cases in config validation
- 4.3.3 Use real file I/O with temporary directories
- 4.3.4 Test serialization round-trips
- 4.3.5 Keep tests focused on one transformation

**Example:**
```python
# GOOD - Complex config validation
def test_config_loader_handles_invalid_tool_paths():
    yaml_content = """
    name: test
    tools:
      - module: nonexistent.module
    """
    with pytest.raises(ToolDiscoveryError):
        load_config_from_yaml(yaml_content)

# SKIP - Simple property access
def test_agent_config_has_name():  # Don't write this!
    config = AgentConfig(name="test")
    assert config.name == "test"
```

## 5.0 E2E Tests (Smoke Tests) [E2E-TEST]

### 5.1 Purpose [E2E-PURPOSE]
E2E tests verify critical user workflows work end-to-end with real backends (CLI, Flet, Server) and catch integration issues.

### 5.2 When to Write E2E Tests [E2E-WHEN]
- 5.2.1 CLI mode: agent initialization and conversation
- 5.2.2 Server mode: WebSocket connection and message flow
- 5.2.3 Safe agent approval workflow
- 5.2.4 Session resumption
- 5.2.5 Studio API critical paths (create evaluation, run optimization)

### 5.3 Best Practices [E2E-BEST]
- 5.3.1 Keep E2E tests minimal (5-10 total)
- 5.3.2 Focus on happy paths only
- 5.3.3 Use for smoke testing deployments
- 5.3.4 Don't test edge cases here
- 5.3.5 Accept they'll be slower
- 5.3.6 Mock external APIs only (OpenAI)

**Example:**
```python
def test_cli_agent_conversation_flow():
    """Smoke test: Can we run a basic agent conversation via CLI?"""
    result = subprocess.run([
        "python", "-m", "omniagents.cli", "run",
        "-c", "examples/basic_yaml/agent.yaml",
        "--mode", "cli",
        "--message", "Hello"
    ], capture_output=True)

    assert result.returncode == 0
    assert "session_id" in result.stdout
```

## 6.0 Test Data Management [DATA-MGT]

### 6.1 YAML Configs as Fixtures [DATA-FIX]
- 6.1.1 Use minimal YAML strings for agent configs
- 6.1.2 Keep configs focused on what's being tested
- 6.1.3 Avoid complex shared config fixtures
- 6.1.4 Prefer inline config over external files

### 6.2 Session and Database Management [DATA-DB]
- 6.2.1 Use temporary SQLite databases per test
- 6.2.2 Clean up sessions after tests
- 6.2.3 Use `pytest` fixtures for database setup
- 6.2.4 Don't mock the database layer

### 6.3 Mock External Services Only [DATA-MOCK]
- 6.3.1 Mock OpenAI API responses
- 6.3.2 Mock web search/fetch for tools
- 6.3.3 Use real file system with temp directories
- 6.3.4 Use real SQLite, not in-memory mocks

## 7.0 Evaluation Testing [EVAL-TEST]

### 7.1 Key Points to Test [EVAL-KEY]
- 7.1.1 Scenario loading from YAML/datasets
- 7.1.2 Agent adapter execution with context
- 7.1.3 Judge adapter scoring logic
- 7.1.4 Metric calculation (accuracy, F1, etc.)
- 7.1.5 Pipeline orchestration (run evaluation end-to-end)
- 7.1.6 Optimization parameter search

### 7.2 Best Practices [EVAL-BEST]
- 7.2.1 Test evaluation pipeline through integration tests
- 7.2.2 Use real scenarios, real agents, mocked LLM calls
- 7.2.3 Test metric calculations in unit tests
- 7.2.4 Verify evaluation results are persisted correctly
- 7.2.5 Test both GEPA agent and judge adapters

## 8.0 Session Isolation Testing [SESSION-TEST]

### 8.1 Purpose [SESSION-PURPOSE]
Session isolation testing ensures agents maintain separate conversation histories and don't leak data between sessions, critical for multi-user scenarios.

### 8.2 Key Aspects to Test [SESSION-ASPECTS]

#### 8.2.1 Data Isolation [SESSION-ISO]
- 8.2.1.1 Sessions cannot access other sessions' messages
- 8.2.1.2 Conversation history is properly scoped to session_id
- 8.2.1.3 Resuming sessions loads correct history

#### 8.2.2 Cross-session Behavior [SESSION-CROSS]
- 8.2.2.1 Creating new session doesn't affect existing sessions
- 8.2.2.2 Concurrent sessions work independently
- 8.2.2.3 Session cleanup doesn't affect active sessions

### 8.3 Best Practices [SESSION-BEST]
- 8.3.1 Test through agent execution interface
- 8.3.2 Create at least two sessions in tests
- 8.3.3 Verify session_id propagation through RPC
- 8.3.4 Test session resumption with --session-id flag

## 9.0 Common Pitfalls to Avoid [PITFALLS]

### 9.1 Over-Testing [PIT-OVER]
- 9.1.1 Testing OpenAI agents library behavior
- 9.1.2 Testing Python dataclass defaults
- 9.1.3 Testing simple property getters/setters
- 9.1.4 Aiming for 100% coverage
- 9.1.5 Too many E2E tests

### 9.2 Under-Testing [PIT-UNDER]
- 9.2.1 Skipping safe agent approval flow
- 9.2.2 Missing RPC protocol edge cases
- 9.2.3 Insufficient evaluation adapter tests
- 9.2.4 Not testing config validation errors

### 9.3 Poor Test Organization [PIT-ORG]
- 9.3.1 Complex mock hierarchies
- 9.3.2 Excessive fixture dependencies
- 9.3.3 Unclear test names
- 9.3.4 Too much setup boilerplate

### 9.4 Wrong Testing Level [PIT-LEVEL]
- 9.4.1 Too many unit tests for simple classes
- 9.4.2 Not enough agent integration tests
- 9.4.3 Too many backend-specific tests
- 9.4.4 Testing UI rendering in unit tests

## 10.0 Testing Priorities by Module [PRIORITIES]

### 10.1 High Priority - Integration Tests [PRI-HIGH]
- **omniagents/core/agents/** - Agent lifecycle, safe agent approval
- **omniagents/rpc/** - Protocol correctness, event streaming
- **omniagents/core/config/** - Config loading and validation
- **omniagents/core/tools/** - Tool discovery and execution
- **omniagents/core/session/** - Session persistence and resumption

### 10.2 Medium Priority - Selective Unit Tests [PRI-MED]
- **omniagents/core/eval/** - Evaluation metrics and adapters
- **omniagents/core/project/** - Project scaffolding logic
- **omniagents/studio/server/api.py** - API endpoint logic

### 10.3 Low Priority [PRI-LOW]
- **Backends** - Minimal smoke tests only
- **Built-in tools** - Basic integration tests
- **Studio tracing** - Core logic only

## 11.0 Conclusion [CONCLUSION]

This testing approach emphasizes pragmatism for agent orchestration:
- [CON-1] **Agent integration tests are your primary layer** - Test through RPC/execution
- [CON-2] **Unit tests only for complex logic** - Config parsing, evaluation, calculations
- [CON-3] **E2E tests for smoke testing** - Critical workflows only
- [CON-4] **Use real agents and tools** - Avoid mocking the agent framework
- [CON-5] **Keep things simple** - Minimal configs, clear intentions

**Remember:** The goal is not to test everything, but to test agent orchestration at the right level. Focus on:
1. Does configuration load correctly?
2. Do tools execute properly?
3. Does the RPC protocol work?
4. Are sessions isolated?
5. Does evaluation work end-to-end?

Test through agent execution interfaces, not individual Python functions.
