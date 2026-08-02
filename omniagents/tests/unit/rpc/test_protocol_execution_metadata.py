from omniagents.rpc.protocol_catalog import (
    OPERATIONS,
    OPERATION_BY_NAME,
    ExecutionRequirement,
    ResourceScope,
)


def test_every_protocol_operation_has_explicit_resource_and_execution_metadata():
    assert len(OPERATIONS) == 127
    assert len(OPERATION_BY_NAME) == len(OPERATIONS)
    assert all(
        isinstance(operation.resource_scope, ResourceScope) for operation in OPERATIONS
    )
    assert all(
        isinstance(operation.execution_requirements, ExecutionRequirement)
        for operation in OPERATIONS
    )


def test_only_server_call_delegates_metadata_to_an_extension_descriptor():
    delegated = {
        operation.name
        for operation in OPERATIONS
        if operation.resource_scope is ResourceScope.EXTENSION
        or ExecutionRequirement.EXTENSION_DECLARED in operation.execution_requirements
    }

    assert delegated == {"server_call"}
    assert (
        OPERATION_BY_NAME["server_call"].execution_requirements
        == ExecutionRequirement.EXTENSION_DECLARED
    )


def test_run_start_derives_environment_requirements_from_the_tool_plan():
    operation = OPERATION_BY_NAME["start_run"]

    assert operation.resource_scope is ResourceScope.THREAD
    assert operation.execution_requirements == ExecutionRequirement.RUN_PLAN


def test_filesystem_operations_are_workspace_scoped():
    operations = [
        operation for operation in OPERATIONS if operation.name.startswith("fs_")
    ]

    assert operations
    for operation in operations:
        assert operation.resource_scope is ResourceScope.WORKSPACE
        if operation.kind == "request":
            assert operation.execution_requirements & (
                ExecutionRequirement.WORKSPACE_READ
                | ExecutionRequirement.WORKSPACE_WRITE
            )
        else:
            assert operation.execution_requirements == ExecutionRequirement.NONE


def test_git_requests_require_workspace_process_execution():
    requests = [
        operation
        for operation in OPERATIONS
        if operation.kind == "request" and operation.name.startswith("git_")
    ]

    assert requests
    for operation in requests:
        assert operation.resource_scope is ResourceScope.WORKSPACE
        assert operation.execution_requirements & ExecutionRequirement.PROCESS
        assert operation.execution_requirements & (
            ExecutionRequirement.WORKSPACE_READ | ExecutionRequirement.WORKSPACE_WRITE
        )

    for name in ("git_fetch", "git_pull", "git_push"):
        assert (
            OPERATION_BY_NAME[name].execution_requirements
            & ExecutionRequirement.NETWORK
        )


def test_notifications_never_acquire_execution_resources():
    notifications = [
        operation for operation in OPERATIONS if operation.kind != "request"
    ]

    assert notifications
    assert all(
        operation.execution_requirements == ExecutionRequirement.NONE
        for operation in notifications
    )
