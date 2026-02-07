# MLflow Tests

Comprehensive MLflow testing framework with Kubernetes RBAC integration for testing MLflow permissions and workspace isolation.

## Description

This project provides a comprehensive testing framework for MLflow with dual-mode support:
- **Local Mode**: Tests MLflow REST API with Basic Authentication
- **Kubernetes Mode**: Tests MLflow with Kubernetes RBAC and ServiceAccount-based authentication

The framework validates user permissions using specific Kubernetes verbs (GET, CREATE, LIST, UPDATE, DELETE) and ensures proper workspace isolation in multi-tenant MLflow deployments. It uses a declarative, TestData-driven approach for comprehensive RBAC testing with automatic cleanup and workspace management.

## Architecture

### Core Components

- **Resource Management**: Enum-based resource definitions (`ResourceType`, `KubeVerb`) with automatic Kubernetes RBAC mapping
- **User Manager**: Kubernetes-based user creation using ServiceAccounts with RBAC
- **Test Framework**: Declarative test scenarios using `TestData`, `TestStep`, and `TestContext` for state management
- **Action System**: Reusable action functions for MLflow operations (create, read, delete)
- **Validation System**: Comprehensive validation functions for success/failure scenarios

### Testing Pattern: TestData-Driven Approach

The framework uses a declarative testing pattern where test scenarios are defined as `TestData` objects containing `TestStep` sequences:

```python
@dataclass
class TestStep:
    action_func: Callable[[TestContext], None]   # Action to perform
    validate_func: Callable[[TestContext], None]  # Validation to run after action
    workspace_to_use: str | None                  # Optional workspace override
    user_info: UserInfo | None                    # Optional per-step user context

@dataclass
class TestData:
    test_name: str                               # Descriptive test name
    test_steps: list[TestStep] | TestStep        # One or more test steps
    user_info: Optional[UserInfo]                # User role and workspace
    workspace_to_use: Optional[str]              # Target workspace
```

Each test follows a consistent execution pattern:
1. **Create user** with specific role/resource permissions
2. **Set authentication context** for the user
3. **Switch workspace** to the test workspace
4. **Execute test steps** — each step runs an action then its paired validation

### Directory Structure

```text
mlflow-tests/
├── src/mlflow_tests/          # Core reusable package
│   ├── enums/                 # Resource and permission definitions
│   │   ├── resource_type.py   # MLflow resource types (EXPERIMENTS, REGISTERED_MODELS, JOBS, GATEWAY_*)
│   │   └── kube_verb.py       # Kubernetes verbs (GET, CREATE, LIST, UPDATE, DELETE)
│   ├── manager/               # Kubernetes user and resource management
│   │   ├── namespace.py       # Namespace management
│   │   ├── rbac.py            # K8s role and role binding management
│   │   ├── service_account.py # ServiceAccount and token management
│   │   └── user.py            # User creation via ServiceAccounts
│   └── utils/                 # Utility functions
│       └── client.py          # Kubernetes and MLflow client factories
├── tests/                     # Test suite
│   ├── actions/               # Reusable action functions
│   │   ├── experiment_actions.py
│   │   ├── model_actions.py
│   │   └── artifact_actions.py
│   ├── validations/           # Validation functions
│   │   ├── experiment_validations.py
│   │   ├── model_validations.py
│   │   ├── artifact_validations.py
│   │   └── validation_utils.py
│   ├── shared/                # Test data structures
│   │   ├── test_context.py    # Runtime state management
│   │   ├── test_data.py       # TestData and TestStep definitions
│   │   ├── user_info.py       # User information object
│   │   └── error_models.py    # Structured error response models
│   ├── constants/
│   │   └── config.py          # Configuration from environment variables
│   ├── base.py                # TestBase class with fixtures and step execution
│   ├── conftest.py            # Pytest fixtures
│   ├── test_experiments.py    # Experiment permission tests
│   ├── test_models.py         # Registered model permission tests
│   └── test_artifacts.py      # Artifact and model logging tests
└── pyproject.toml
```

## Installation

```bash
uv sync
```

## Configuration

The framework supports configuration via environment variables:

| Variable | Description | Default | Mode |
|----------|-------------|---------|------|
| `LOCAL` | Use local MLflow mode (vs Kubernetes) | `false` | Both |
| `admin_uname` | MLflow admin username | Required | Local |
| `admin_pass` | MLflow admin password | Required | Local |
| `kube_token` | Kubernetes bearer token | Required | K8s |
| `MLFLOW_TRACKING_URI` | MLflow tracking server URI | `https://localhost:8080` | Both |
| `workspaces` | Comma-separated workspace list | `workspace1,workspace2` | Both |
| `DISABLE_TLS` | Disable TLS verification | `true` | Both |
| `artifact_storage` | Artifact storage type (`s3` or `file`) | `file` | Both |
| `serve_artifacts` | Whether MLflow serves artifacts | `true` | Both |
| `MLFLOW_BACKEND_STORE_URI` | Database connection URI for store cleanup | `postgresql://postgres:mysecretpassword@localhost:5432/mydatabase` | Both |
| `MLFLOW_S3_ENDPOINT_URL` | S3 endpoint URL | Optional | Both |
| `AWS_ACCESS_KEY_ID` | AWS access key for S3 | Optional | Both |
| `AWS_SECRET_ACCESS_KEY` | AWS secret key for S3 | Optional | Both |

**Required Setup:**
- **Kubernetes Mode**: Requires `MLFLOW_TRACKING_URI`, `workspaces`, and `kube_token`
- **Local Mode**: Requires `MLFLOW_TRACKING_URI`, `workspaces`, `admin_uname`, and `admin_pass`

## Usage

### Running Tests

```bash
# Run all tests (debug logging is enabled by default)
uv run pytest

# Run specific test files
uv run pytest tests/test_experiments.py
uv run pytest tests/test_models.py
uv run pytest tests/test_artifacts.py

# Run with specific markers
uv run pytest -m Experiments    # Experiment RBAC tests
uv run pytest -m Models         # Registered model RBAC tests
uv run pytest -m Artifacts      # Artifact operations and S3 storage tests

# Run in local mode (bypasses Kubernetes)
LOCAL=true uv run pytest

# Override log level (default is DEBUG, configured in pyproject.toml)
uv run pytest --log-cli-level=INFO

# Run specific test scenario
uv run pytest tests/test_experiments.py -k "GET permission can get experiment"
```

### Test Markers

The framework defines three custom pytest markers:

- **`@pytest.mark.Experiments`**: Test experiment RBAC and management operations
- **`@pytest.mark.Models`**: Test registered model RBAC and management operations
- **`@pytest.mark.Artifacts`**: Test artifact operations, model logging, and S3 storage verification

### Test Execution Workflow

Each test follows this execution flow:

1. **Session Setup** (once per test session):
   - Initialize Kubernetes/MLflow clients
   - Create test namespaces/workspaces
   - Create baseline resources per workspace (experiments, registered models)
   - Store resource map for test use

2. **Per-Test Execution**:
   - Initialize instance attributes (clients, context)
   - Setup admin authentication context
   - Pre-test cleanup (orphaned runs, disable autologging)
   - Create test user with role/permissions
   - Set user authentication context with authenticated MLflow client
   - Execute test steps via `_execute_test_steps()`:
     - For each step: execute action (capture exception as `ErrorResponse`), then validation
   - Cleanup (delete created resources in correct workspaces)
   - Restore original workspace

### Test Output

Debug logging is enabled by default via `pyproject.toml`. Tests provide detailed logging showing:
- Step-by-step execution progress
- User creation with specific permissions and role details
- Workspace context switching and namespace operations
- Action execution (success/failure with error details)
- RBAC permission verification and retry logic
- Validation results with specific assertion details
- Resource cleanup operations and status

## Features

### Dual-Mode Architecture
- **Kubernetes Mode**: ServiceAccount + RBAC + Bearer Token authentication
- **Local Mode**: MLflow REST API + Basic Authentication
- Automatic mode detection via environment variables
- Pluggable user manager pattern for extensible authentication backends

### Permission Testing Matrix
Tests use specific Kubernetes verbs for granular permission control:
- **GET**: Can retrieve specific resources (`get` verb)
- **LIST**: Can list resources (`list` verb)
- **CREATE**: Can create new resources (`create` verb)
- **UPDATE**: Can modify existing resources (`update` verb)
- **DELETE**: Can delete resources (`delete` verb)
- **Gateway Subresources**: Special permissions for model serving (gatewaysecrets/use, etc.)

### MLflow Operator Integration
- **Resource Types**: Maps to Kubernetes CustomResources (`experiments`, `registeredmodels`, `jobs`, `gateway*`)
- **RBAC Enforcement**: ServiceAccount-based authentication with Role/RoleBinding creation
- **Workspace Isolation**: Multi-tenant MLflow deployments with workspace-scoped permissions
- **Model Serving**: Gateway resource permissions for MLflow model serving features

### Comprehensive Test Coverage
- **Experiment Operations**: Create, read, delete experiments with RBAC validation
- **Model Management**: Registered model lifecycle with permission enforcement
- **Artifact Storage**: S3 integration testing for model artifacts and logging operations
- **Cross-Workspace Security**: Validates users cannot access resources in other workspaces
- **Permission Matrix**: Tests all role levels against all operations (success and failure scenarios)

### Advanced Workflow Features
- **Automatic Resource Cleanup**: Workspace-aware cleanup of experiments, models, runs, and users
- **Retry Logic**: Exponential backoff for Kubernetes token handling and RBAC propagation
- **Error Isolation**: Test failures don't cascade to cleanup operations
- **State Management**: Centralized TestContext for tracking resources across test execution
- **Fixture-Based Setup**: Session-scoped and test-scoped fixtures for efficient resource management

### Testing Infrastructure
- **Declarative Test Definition**: TestData-driven approach with reusable action/validation functions
- **Parameterized Testing**: Single test methods handle multiple permission scenarios
- **Context-Aware Logging**: Detailed step-by-step logging with credential masking
- **Baseline Resource Creation**: Pre-created experiments and models for consistent testing
- **Multi-Step Workflows**: Complex test scenarios with sequences of action/validation pairs

### Error Handling

The framework provides generic error surfacing in `TestBase._execute_validation()`:

- When an action fails, the exception is captured as a structured `ErrorResponse` in `test_context.last_error`
- If a subsequent validation (other than `validate_authentication_denied`) runs while `last_error` is set, the framework raises the actual action error instead of letting the validation produce a misleading message
- This ensures test failures always show the root cause (e.g., `PERMISSION_DENIED`) rather than a symptom (e.g., "Model URI not set")

### Key Architectural Patterns

#### TestData-Driven Testing
Tests define scenarios as data structures rather than imperative code:
```python
test_scenarios = [
    TestData(
        test_name="User with UPDATE permission can log and download artifacts",
        user_info=UserInfo(
            workspace=Config.WORKSPACES[0],
            verbs=[KubeVerb.GET, KubeVerb.UPDATE, KubeVerb.LIST],
            resource_types=[ResourceType.EXPERIMENTS]
        ),
        workspace_to_use=Config.WORKSPACES[0],
        test_steps=[
            TestStep(action_func=action_start_run, validate_func=validate_run_created),
            TestStep(action_func=action_create_temp_artifact),
            TestStep(action_func=action_log_artifact),
            TestStep(action_func=action_list_artifacts, validate_func=validate_artifact_logged),
            TestStep(action_func=action_download_artifact, validate_func=validate_artifact_downloaded),
            TestStep(action_func=action_end_run, validate_func=validate_run_ended),
        ]
    ),
]
```

#### Per-Step User Context
Individual steps can override the user context, enabling tests where different users perform different actions within the same test:
```python
TestData(
    test_name="User with GET permission cannot end run",
    workspace_to_use=Config.WORKSPACES[0],
    test_steps=[
        TestStep(
            action_func=action_start_run,
            validate_func=validate_run_created,
            user_info=UserInfo(workspace=Config.WORKSPACES[0], verbs=[KubeVerb.UPDATE], resource_types=[ResourceType.EXPERIMENTS])
        ),
        TestStep(
            action_func=action_end_run,
            validate_func=validate_authentication_denied,
            user_info=UserInfo(workspace=Config.WORKSPACES[0], verbs=[KubeVerb.GET], resource_types=[ResourceType.EXPERIMENTS])
        ),
    ]
),
```

#### Kubernetes User Management
- `K8UserManager`: Creates Kubernetes ServiceAccounts with RBAC roles and returns authenticated tokens
- Each test user gets a dedicated ServiceAccount, Role, and RoleBinding
- Authenticated `MlflowClient` instances are created per user and stored in `UserInfo.client`

#### Context-Based State Management
- `TestContext`: Centralized state tracking across test step execution
- Maintains active resources (experiments, models, runs, users) with workspace context
- Automatic cleanup lists with proper workspace switching
- Structured error capture via `ErrorResponse` for validation in failure scenarios

#### Action-Validation Separation
- **Action Functions**: Perform MLflow operations, let exceptions propagate (caught by base framework)
- **Validation Functions**: Inspect TestContext state, raise `AssertionError` on failure
- Workspace context is set by the test framework before step execution — actions do not need to call `mlflow.set_workspace()` themselves
- Actions and validations are paired within `TestStep` objects for clear intent

---

## Testing Philosophy

### Permission Matrix Approach
Every operation is tested with all relevant permission levels:
- **Success scenarios**: User has sufficient permissions
- **Failure scenarios**: User lacks required permissions
- **Cross-workspace violations**: User attempts access outside assigned workspace

### Workspace-First Design
All operations are workspace-aware:
- Users are assigned to specific workspaces during creation
- The test framework sets workspace context before executing steps
- Cleanup respects workspace boundaries
- Baseline resources exist in each configured workspace

### Error Handling Strategy
- **Capture, don't fail**: Action exceptions are captured as structured `ErrorResponse` in `test_context.last_error`
- **Generic error surfacing**: The base framework surfaces action errors in validation failures automatically
- **Retry with backoff**: Kubernetes operations use exponential backoff
- **Isolation**: Test failures don't prevent cleanup operations
- **Graceful degradation**: Missing resources logged but don't halt execution

---

## Contributors Guide

### Understanding the Test Framework

This framework uses a declarative approach where tests are defined as `TestData` objects containing `TestStep` sequences that specify:
- Action to perform
- Validation to execute
- Optional per-step user permissions
- Optional per-step workspace

#### Key Data Structures

**TestStep** (`tests/shared/test_data.py`):
```python
@dataclass
class TestStep:
    action_func: Callable[[TestContext], None] = None   # Action to perform
    validate_func: Callable[[TestContext], None] = None  # Validation after action
    workspace_to_use: str | None = None                  # Optional workspace override
    user_info: UserInfo | None = None                    # Optional per-step user
```

**TestData** (`tests/shared/test_data.py`):
```python
@dataclass
class TestData:
    test_name: str                                       # Descriptive test name
    test_steps: list[TestStep] | TestStep                # Steps to execute
    user_info: Optional[UserInfo] = None                 # Default user for all steps
    workspace_to_use: Optional[str] = None               # Default workspace
```

**TestContext** (`tests/shared/test_context.py`):
```python
@dataclass
class TestContext:
    workspaces: list[str]                    # Available workspaces
    active_workspace: Optional[str]          # Current workspace
    active_user: Optional[UserInfo]          # Current authenticated user
    user_client: Optional[MlflowClient]      # Authenticated MLflow client
    active_experiment_id: Optional[str]      # Current experiment ID
    experiments_to_delete: dict[str, str]    # experiment_id -> workspace
    current_run_id: Optional[str]            # Current MLflow run ID
    runs_to_delete: dict[str, str]           # run_id -> workspace
    active_model_name: Optional[str]         # Current registered model name
    models_to_delete: dict[str, str]         # model_name -> workspace
    users_to_delete: list[UserInfo]          # Users to clean up
    resource_map: dict                       # Session-scoped baseline resources
    last_error: Optional[ErrorResponse]      # Structured error from last action
    # Artifact-specific fields
    temp_artifact_path: Optional[str]        # Temp artifact file path
    temp_artifact_content: Optional[str]     # Temp artifact content
    artifact_list: Optional[list]            # Listed artifacts
    downloaded_path: Optional[str]           # Downloaded artifact path
    model: Optional[Any]                     # Created/loaded model object
    model_uri: Optional[str]                 # Logged model URI
    artifact_location: Optional[str]         # Artifact storage URI
```

**UserInfo** (`tests/shared/user_info.py`):
```python
class UserInfo:
    uname: Optional[str]                     # Username
    upass: Optional[str]                     # Password/token (masked in logs)
    workspace: Optional[str]                 # User's assigned workspace
    resource_types: Optional[list[ResourceType]]  # Resource permissions
    verbs: Optional[list[KubeVerb]]          # Kubernetes verbs
    subresources: Optional[list[str]]        # Optional subresources
    client: Optional[MlflowClient]           # Authenticated MLflow client
```

### Adding Tests to Existing Test Files

#### Step 1: Define Test Scenarios

Add new `TestData` objects to the `test_scenarios` list in your test class:

```python
test_scenarios = [
    # Existing scenarios...

    # Success scenario
    TestData(
        test_name="User with UPDATE permission can update experiment tags",
        user_info=UserInfo(
            workspace=Config.WORKSPACES[0],
            verbs=[KubeVerb.UPDATE],
            resource_types=[ResourceType.EXPERIMENTS]
        ),
        workspace_to_use=Config.WORKSPACES[0],
        test_steps=TestStep(
            action_func=action_update_experiment_tags,
            validate_func=validate_experiment_tags_updated,
        ),
    ),
    # Failure scenario
    TestData(
        test_name="User with GET permission cannot update experiment tags",
        user_info=UserInfo(
            workspace=Config.WORKSPACES[0],
            verbs=[KubeVerb.GET],
            resource_types=[ResourceType.EXPERIMENTS]
        ),
        workspace_to_use=Config.WORKSPACES[0],
        test_steps=TestStep(
            action_func=action_update_experiment_tags,
            validate_func=validate_authentication_denied,
        ),
    ),
]
```

#### Step 2: Create Action Functions

Create new action functions in the appropriate `actions/` file. Actions should:
- Accept only `test_context: TestContext` as an argument
- Modify `test_context` state as needed
- Let exceptions propagate (the base framework captures them)
- Add created resources to cleanup lists

```python
# In tests/actions/experiment_actions.py
def action_update_experiment_tags(test_context: TestContext) -> None:
    """Update tags on the active experiment."""
    tags = {"updated_by": test_context.active_user.uname, "test_tag": "test_value"}

    for key, value in tags.items():
        test_context.user_client.set_experiment_tag(
            test_context.active_experiment_id,
            key,
            value
        )
    logger.info(f"Updated experiment {test_context.active_experiment_id} with tags: {tags}")
```

#### Step 3: Create Validation Functions

Create corresponding validation functions in the `validations/` directory. Validations should:
- Accept only `test_context: TestContext` as an argument
- Raise `AssertionError` on failure
- Focus on verifying state — the base framework handles surfacing action errors automatically

```python
# In tests/validations/experiment_validations.py
def validate_experiment_tags_updated(test_context: TestContext) -> None:
    """Validate that experiment tags were successfully updated."""
    assert test_context.active_experiment_id is not None, \
        "No active experiment to validate tags on"

    experiment = test_context.user_client.get_experiment(test_context.active_experiment_id)

    expected_tags = {"updated_by": test_context.active_user.uname, "test_tag": "test_value"}
    for key, expected_value in expected_tags.items():
        assert key in experiment.tags, f"Expected tag '{key}' not found"
        assert experiment.tags[key] == expected_value, \
            f"Tag '{key}' has value '{experiment.tags[key]}', expected '{expected_value}'"

    logger.info(f"Validated experiment tags: {expected_tags}")
```

#### Step 4: Import and Run

Add imports to your test file and the `actions/__init__.py` / `validations/__init__.py`:

```python
# In test_experiments.py
from .actions import action_update_experiment_tags
from .validations import validate_experiment_tags_updated
```

### Creating New Test Files

```python
# tests/test_new_feature.py
import logging
import pytest
from typing import ClassVar

from .shared import UserInfo, TestData, TestStep
from .constants.config import Config
from .actions import action_your_new_action
from .validations import validate_your_new_validation, validate_authentication_denied

from mlflow_tests.enums import ResourceType, KubeVerb
from .base import TestBase

logger = logging.getLogger(__name__)

@pytest.mark.YourNewFeature
class TestYourNewFeature(TestBase):
    """Test your new feature with RBAC permissions."""

    test_scenarios: ClassVar[list[TestData]] = [
        TestData(
            test_name="User with GET permission can perform read operation",
            user_info=UserInfo(
                workspace=Config.WORKSPACES[0],
                verbs=[KubeVerb.GET],
                resource_types=[ResourceType.EXPERIMENTS]
            ),
            workspace_to_use=Config.WORKSPACES[0],
            test_steps=TestStep(
                action_func=action_your_new_action,
                validate_func=validate_your_new_validation,
            ),
        ),
        TestData(
            test_name="User with GET permission on workspace 1 cannot access workspace 2",
            user_info=UserInfo(
                workspace=Config.WORKSPACES[0],
                verbs=[KubeVerb.GET],
                resource_types=[ResourceType.EXPERIMENTS]
            ),
            workspace_to_use=Config.WORKSPACES[1],
            test_steps=TestStep(
                action_func=action_your_new_action,
                validate_func=validate_authentication_denied,
            ),
        ),
    ]

    @pytest.mark.parametrize('test_data', test_scenarios, ids=lambda x: x.test_name)
    def test_your_new_feature(self, create_user_with_permissions, test_data: TestData):
        """Test your new feature operations with user permissions."""
        logger.info("=" * 80)
        logger.info(f"Starting test: {test_data.test_name}")
        logger.info("=" * 80)

        self.test_context.last_error = None

        if test_data.user_info:
            user_info: UserInfo = create_user_with_permissions(
                workspace=test_data.user_info.workspace,
                verbs=test_data.user_info.verbs,
                resource_types=test_data.user_info.resource_types,
                subresources=test_data.user_info.subresources
            )
            self.test_context.active_user = user_info
            self.test_context.user_client = user_info.client

        if test_data.workspace_to_use:
            import mlflow
            self.test_context.active_workspace = test_data.workspace_to_use
            mlflow.set_workspace(self.test_context.active_workspace)

        self._execute_test_steps(test_data)

        logger.info(f"Test PASSED: {test_data.test_name}")
```

### Best Practices

#### 1. Test Naming Convention
- Use descriptive names that clearly state the permission level, operation, and expected result
- Format: `"User with {VERB} permission {can/cannot} {operation} {additional_context}"`

#### 2. Action Functions
- Accept only `test_context: TestContext` — no other parameters
- Let exceptions propagate — the base framework captures them as `ErrorResponse`
- Do NOT manually set `test_context.last_error` — the framework handles this
- Do NOT call `mlflow.set_workspace()` — the framework sets workspace before step execution
- Add created resources to cleanup lists using `test_context.add_*_for_cleanup()`
- Use comprehensive logging for debugging

#### 3. Validation Functions
- Focus on asserting expected state in `test_context`
- Do NOT check `test_context.last_error` for success validations — the base framework surfaces action errors automatically before running the validation
- Use `validate_authentication_denied` for expected permission failures
- Use specific assertion messages that explain what was expected vs actual

#### 4. Resource Cleanup
- Always add created resources to cleanup lists:
  ```python
  test_context.add_experiment_for_cleanup(experiment_id, workspace)
  test_context.add_model_for_cleanup(model_name, workspace)
  test_context.add_run_for_cleanup(run_id, workspace)
  test_context.add_user_for_cleanup(user_info)
  ```

#### 5. Workspace Isolation Testing
- Test cross-workspace permission failures by using different `workspace_to_use` vs `user_info.workspace`
- Validate that operations fail when user tries to access resources in other workspaces

#### 6. Permission Matrix Testing
- Test each operation with all relevant permission levels (GET, CREATE, UPDATE, DELETE)
- Include both success and failure scenarios
- Test workspace boundary violations

### Testing Your Changes

```bash
# Run your new tests
uv run pytest tests/test_your_new_feature.py -v

# Override default DEBUG log level if too verbose
uv run pytest tests/test_your_new_feature.py --log-cli-level=INFO

# Run specific test scenarios
uv run pytest tests/test_your_new_feature.py -k "GET permission"
```

## Troubleshooting

### Common Issues and Solutions

#### Kubernetes Token Errors
**Problem**: Tests fail with authentication errors in K8s mode
**Solution**:
- Verify `kube_token` is valid and has sufficient permissions
- Check if ServiceAccount tokens are being created successfully
- Tests include automatic retry with exponential backoff (up to 15 retries)

#### RBAC Permission Delays
**Problem**: Tests fail because permissions haven't propagated yet
**Solution**:
- Framework includes built-in retry logic for permission verification
- Increase retry counts in config if running on slow clusters
- Ensure Kubernetes RBAC is properly configured

#### Workspace Access Issues
**Problem**: Tests fail with workspace not found errors
**Solution**:
- Verify all workspaces in `workspaces` env var exist in MLflow server
- Check MLflow server has multi-tenant workspace support enabled
- Ensure admin user has access to create resources in all workspaces

#### Resource Cleanup Failures
**Problem**: Tests leave orphaned resources
**Solution**:
- Framework includes comprehensive cleanup with error isolation
- Manual cleanup can be done by deleting test namespaces (K8s mode)
- Check logs for specific cleanup error details

#### Artifact Storage Tests Failing
**Problem**: Artifact tests fail with S3 errors
**Solution**:
- Verify `artifact_storage` configuration is correct
- Ensure MLflow server has proper S3 backend configuration
- Check network connectivity to S3-compatible storage

#### Misleading Validation Errors
**Problem**: Validation error says "Model URI not set" instead of showing the actual permission error
**Solution**:
- This is handled automatically by the framework — `_execute_validation()` in `base.py` surfaces the actual action error when `last_error` is set
- If you see misleading errors, ensure your validation function name is not `validate_authentication_denied` (which is the only validation that expects errors)

### Debug Commands

```bash
# Run with default DEBUG logging (configured in pyproject.toml)
uv run pytest -v -s

# Run with reduced logging
uv run pytest --log-cli-level=INFO

# Run single test with detailed output
uv run pytest tests/test_experiments.py -k "GET permission can get experiment" -v -s

# Check Kubernetes resources
kubectl get serviceaccounts -n <workspace>
kubectl get roles,rolebindings -n <workspace>

# Verify MLflow workspace access
curl -k -H "Authorization: Bearer $kube_token" "$MLFLOW_TRACKING_URI/api/2.0/mlflow/experiments/list"
```

### Performance Tuning

- **Session-scoped fixtures**: Reduce test setup overhead by reusing clients and baseline resources
- **Parallel execution**: Use `pytest-xdist` for parallel test execution (ensure sufficient K8s resources)
- **Selective test runs**: Use markers to run only specific test categories
- **Local mode**: Use `LOCAL=true` for faster testing without Kubernetes overhead

## Requirements

### Core Dependencies
- **Python**: 3.13+ (required for latest language features)
- **uv package manager**: For reproducible environment management
- **pytest**: >=9.0.2 (testing framework)

### MLflow Dependencies
- **MLflow**: Custom fork from `git+https://github.com/opendatahub-io/mlflow@master`
  - Includes custom authentication with workspace support
  - Bearer token authentication for Kubernetes integration
  - Multi-tenant workspace isolation features

### Kubernetes Dependencies (K8s Mode)
- **kubernetes**: <35.0.0 (Python client for Kubernetes API)
- **Kubernetes cluster**: With RBAC enabled and admin access
- **KUBECONFIG**: Valid kubeconfig or in-cluster configuration
- **MLflow Operator**: Deployed in cluster with CustomResource definitions

### Runtime Environment
- **MLflow Tracking Server**: With authentication enabled and workspace support
- **S3-Compatible Storage**: For artifact tests (configurable)
- **Network Access**: To MLflow server and Kubernetes API

### Optional Dependencies
- **scikit-learn**: Required for artifact/model logging tests
- **Flask-WTF**: <2 (transitive dependency of MLflow auth)
- **boto3**: >=1.42.40 (for S3 artifact storage)
- **pydantic**: >=2.0.0 (for structured error models)

### Deployment Requirements
**Kubernetes Mode:**
- MLflow server deployed with operator-based authentication
- Kubernetes cluster with CustomResource definitions installed
- RBAC permissions for ServiceAccount and Role creation
- Network policies allowing MLflow server communication

**Local Mode:**
- MLflow server with basic authentication enabled
- Admin user credentials for user management
- Direct network access to MLflow API endpoints
