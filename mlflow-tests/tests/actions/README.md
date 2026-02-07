# Test Actions Architecture

## Overview

This directory contains action modules that encapsulate state-modifying operations for MLflow tests. Actions follow a strict single-argument interface pattern to promote consistency and testability.

## Design Principles

### 1. Single Argument Interface
All action functions accept **only one argument**: `test_context: TestContext`

```python
def action_create_experiment(test_context: TestContext) -> None:
    """Create a new experiment."""
    # Implementation reads from and updates test_context
```

### 2. Separation of Concerns
- **Actions**: Modify state (create, update, delete operations)
- **Validations**: Verify state (assert conditions)
- **Test Context**: State container (shared data)

### 3. State Management
Actions interact with `TestContext` to:
- Read configuration (workspace, user, resources)
- Update results (experiment_id, run_id, etc.)
- Track cleanup (experiments_to_delete, runs_to_delete)

## Action Function Contract

### Signature
```python
def action_name(test_context: TestContext) -> None:
    """Brief description.

    Args:
        test_context: Test context containing state and configuration.
                     Updates specific fields based on action.

    Raises:
        Exception: Propagates MLflow API exceptions.
    """
```

### Responsibilities
1. **Read from test_context**: Access active_workspace, active_user, resource_map
2. **Perform operation**: Execute MLflow API calls
3. **Update test_context**: Store results in appropriate fields
4. **Track cleanup**: Add created resources to deletion lists

### Example: action_create_experiment
```python
def action_create_experiment(test_context: TestContext) -> None:
    """Create a new experiment and store its ID in test context."""
    experiment_name = f"test-experiment-{random_gen.randint(0, 1000)}"
    experiment_id = mlflow.create_experiment(experiment_name)

    # Update context with results
    test_context.active_experiment_id = experiment_id
    test_context.experiments_to_delete.append(experiment_id)
```

## Error Handling

Actions **do not** catch exceptions. Exceptions propagate to the test harness where:
1. Test method catches exceptions
2. Stores exception in `test_context.last_error`
3. Validation functions verify expected success/failure

```python
# In test method
try:
    action_func(test_context)
except Exception as e:
    test_context.last_error = e

# In validation
assert test_context.last_error is None, "Action should have succeeded"
```

## Current Actions

### experiment_actions.py
- `action_get_experiment`: Retrieve existing experiment
- `action_create_experiment`: Create new experiment
- `action_delete_experiment`: Delete experiment and verify

## Adding New Actions

### Step 1: Create Action Function
```python
def action_new_operation(test_context: TestContext) -> None:
    """Brief description of what this action does.

    Args:
        test_context: Test context. Updates field_name with result.

    Raises:
        Exception: Relevant MLflow exceptions.
    """
    # Read from context
    workspace = test_context.active_workspace

    # Perform operation
    result = mlflow.some_operation(workspace)

    # Update context
    test_context.some_field = result
```

### Step 2: Export from __init__.py
```python
from .module_name import action_new_operation

__all__ = [
    # ... existing exports
    "action_new_operation",
]
```

### Step 3: Create Validation Function
```python
def validate_new_operation(test_context: TestContext) -> None:
    """Validate the new operation succeeded."""
    assert test_context.last_error is None, "Operation failed"
    assert test_context.some_field is not None, "Result not set"
```

### Step 4: Use in Test Scenario
```python
TestData(
    test_name="Test new operation",
    user_info=UserInfo(...),
    workspace_to_use=workspace,
    action_func=action_new_operation,
    validate_func=validate_new_operation,
)
```

## Benefits

### Consistency
- Uniform interface across all actions
- Predictable behavior and testing patterns

### Testability
- Actions are pure functions of TestContext
- Easy to unit test in isolation
- No hidden dependencies

### Reusability
- Actions can be composed in different scenarios
- Same action can be validated differently
- Supports complex test workflows

### Maintainability
- Clear separation between action and validation
- Single responsibility principle
- Easy to add new operations

## Test Flow

```
┌─────────────────────┐
│  Test Method        │
│  - Setup context    │
│  - Set user/workspace│
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│  Action Function    │
│  - Read context     │
│  - Perform operation│
│  - Update context   │
└──────────┬──────────┘
           │ (exception?)
           ▼
┌─────────────────────┐
│  Exception Handler  │
│  - Store in context │
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│  Validation Function│
│  - Check error      │
│  - Verify state     │
│  - Assert conditions│
└─────────────────────┘
```

## Anti-Patterns to Avoid

### Multiple Arguments
```python
# DON'T
def action_bad(test_context: TestContext, workspace: str, user: UserInfo):
    pass

# DO
def action_good(test_context: TestContext):
    workspace = test_context.active_workspace
    user = test_context.active_user
```

### Mixing Action and Validation
```python
# DON'T
def action_bad(test_context: TestContext):
    result = mlflow.create_experiment("name")
    assert result is not None  # Validation in action
    test_context.experiment_id = result

# DO
def action_good(test_context: TestContext):
    result = mlflow.create_experiment("name")
    test_context.experiment_id = result

def validate_good(test_context: TestContext):
    assert test_context.experiment_id is not None
```

### Direct Exception Handling in Actions
```python
# DON'T
def action_bad(test_context: TestContext):
    try:
        result = mlflow.create_experiment("name")
    except Exception:
        test_context.last_error = "Failed"  # Don't handle exceptions

# DO - Let exceptions propagate
def action_good(test_context: TestContext):
    result = mlflow.create_experiment("name")
    test_context.experiment_id = result
```

## Future Extensions

Potential additions to the actions architecture:

1. **Run Actions**: `action_create_run`, `action_log_metrics`, `action_end_run`
2. **Model Actions**: `action_register_model`, `action_transition_stage`
3. **Artifact Actions**: `action_log_artifact`, `action_download_artifact`
4. **Permission Actions**: `action_grant_permission`, `action_revoke_permission`

Each would follow the same single-argument pattern with TestContext.
