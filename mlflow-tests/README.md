# MLflow Tests

Comprehensive MLflow testing framework with Kubernetes RBAC integration for testing MLflow permissions and workspace isolation.

## Description

This project provides a comprehensive testing framework for MLflow with dual-mode support:
- **Local Mode**: Tests MLflow REST API with Basic Authentication
- **Kubernetes Mode**: Tests MLflow with Kubernetes RBAC and ServiceAccount-based authentication

The framework validates user permissions across different roles (READ, EDIT, USE, MANAGE) and ensures proper workspace isolation in multi-tenant MLflow deployments. It uses a declarative, TestData-driven approach for comprehensive RBAC testing with automatic cleanup and workspace management.

## Architecture

### Core Components

- **Resource Management**: Enum-based resource definitions (`ResourceType`, `UserRole`) with automatic Kubernetes RBAC mapping
- **User Managers**: Pluggable backends for user creation (Kubernetes ServiceAccounts vs MLflow users)
- **Test Framework**: Declarative test scenarios using `TestData` and `TestContext` for state management
- **Action System**: Reusable action functions for MLflow operations (create, read, delete)
- **Validation System**: Comprehensive validation functions for success/failure scenarios

### Testing Pattern: TestData-Driven Approach

The framework uses a declarative testing pattern where test scenarios are defined as `TestData` objects:

```python
@dataclass
class TestData:
    test_name: str                    # Descriptive test name
    user_info: UserInfo              # User role and workspace
    workspace_to_use: str            # Target workspace
    action_func: Callable | list[Callable]  # Single or multiple actions
    validate_func: Callable | list[Callable]  # Single or multiple validators
```

Each test follows a consistent 5-step execution pattern:
1. **Create user** with specific role/resource permissions
2. **Set authentication context** for the user
3. **Switch workspace** to the test workspace
4. **Execute action(s)** (can be single or sequence)
5. **Execute validation(s)** (can be single or sequence)

### Directory Structure

```
mlflow-tests/
├── src/mlflow_tests/          # Core reusable package
│   ├── enums/                 # Resource and role definitions
│   │   ├── resource_type.py   # MLflow resource types (EXPERIMENTS, REGISTERED_MODELS, RUNS, GATEWAY_*)
│   │   └── user_role.py       # User permission levels (READ, EDIT, USE, MANAGE)
│   ├── managers/              # Backend-agnostic user and resource managers
│   │   ├── base.py            # Abstract UserManager interface
│   │   ├── k8s/               # Kubernetes implementation
│   │   │   ├── namespace.py
│   │   │   ├── rbac.py        # K8s role and role binding management
│   │   │   ├── service_account.py
│   │   │   └── user.py        # K8s-specific user creation via ServiceAccounts
│   │   └── mlflow/            # MLflow-native implementation
│   │       └── user.py        # MLflow REST API user management
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
│   │   ├── test_data.py       # Test scenario definition
│   │   └── user_info.py       # User information object
│   ├── constants/
│   │   └── config.py          # Configuration from environment variables
│   ├── base.py                # TestBase class with fixtures
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
| `ADMIN_USERNAME` | MLflow admin username | Required | Local |
| `ADMIN_PASSWORD` | MLflow admin password | Required | Local |
| `K8_API_TOKEN` | Kubernetes bearer token | Required | K8s |
| `MLFLOW_TRACKING_URI` | MLflow tracking server URI | `https://localhost:8080` | Both |
| `WORKSPACES` | Comma-separated workspace list | `workspace1,workspace2` | Both |
| `KUBE_CONFIG` | Kubernetes config file path | Auto-detected | K8s |
| `ARTIFACT_STORAGE` | S3 storage configuration for artifact tests | Optional | Both |

**Required Setup:**
- **Kubernetes Mode**: Requires `MLFLOW_TRACKING_URI`, `WORKSPACES`, and either `K8_API_TOKEN` or valid kubeconfig
- **Local Mode**: Requires `MLFLOW_TRACKING_URI`, `WORKSPACES`, `ADMIN_USERNAME`, and `ADMIN_PASSWORD`

## Usage

### Running Tests

```bash
# Run all tests
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

# Run with verbose logging
uv run pytest -v -s --log-cli-level=INFO

# Run specific test scenario
uv run pytest tests/test_experiments.py::TestExperiments::test_experiment -k "READ user can get experiment"
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
   - Create baseline resources per workspace
   - Store resource map for test use

2. **Per-Test Execution**:
   - Initialize instance attributes (clients, context)
   - Setup admin authentication context
   - Pre-test cleanup (orphaned runs)
   - Create test user with role/permissions
   - Set user authentication context
   - Execute action sequence (switch workspace, perform operations)
   - Execute validation sequence (inspect results, check expectations)
   - Cleanup (delete created resources)
   - Restore original workspace

### Test Output

Tests provide detailed logging showing:
- Step-by-step execution progress
- User creation with specific permissions and role details
- Workspace context switching and namespace operations
- Action execution (success/failure with error details)
- RBAC permission verification and retry logic
- Validation results with specific assertion details
- Resource cleanup operations and status
- Timing information for debugging slow operations

## Features

### Dual-Mode Architecture
- **Kubernetes Mode**: ServiceAccount + RBAC + Bearer Token authentication
- **Local Mode**: MLflow REST API + Basic Authentication
- Automatic mode detection via environment variables
- Pluggable user manager pattern for extensible authentication backends

### Permission Testing Matrix
- **READ**: Can retrieve resources, cannot modify (`get`, `list` verbs)
- **EDIT**: Can create/delete resources in assigned workspace (`get`, `list`, `create`, `update`, `delete` verbs)
- **USE**: Can use gateway resources for model serving (`get`, `list`, `create` verbs)
- **MANAGE**: Full permissions within workspace (all verbs including admin operations)

### MLflow Operator Integration
- **Resource Types**: Maps to Kubernetes CustomResources (`experiments`, `registeredmodels`, `jobs`, `gateway*`)
- **RBAC Enforcement**: ServiceAccount-based authentication with Role/RoleBinding creation
- **Workspace Isolation**: Multi-tenant MLflow deployments with workspace-scoped permissions
- **Model Serving**: Gateway resource permissions for MLflow model serving features

### Comprehensive Test Coverage
- **Experiment Operations**: Create, read, update, delete experiments with RBAC validation
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
- **Multi-Action/Validation Support**: Complex workflows with sequences of operations

### Key Architectural Patterns

#### TestData-Driven Testing
Tests define scenarios as data structures rather than imperative code:
```python
test_scenarios = [
    TestData(
        test_name="Validate READ user can get experiment",
        user_info=UserInfo(workspace=Config.WORKSPACES[0], role=UserRole.READ, ...),
        workspace_to_use=Config.WORKSPACES[0],
        action_func=action_get_experiment,
        validate_func=validate_experiment_retrieved,
    ),
]
```

#### Pluggable User Managers
- Abstract `UserManager` interface allows different authentication backends
- `K8UserManager`: Creates Kubernetes ServiceAccounts with RBAC
- `MlFlowUserManager`: Uses MLflow REST API for user management
- Factory pattern automatically selects implementation based on `LOCAL` environment variable

#### Context-Based State Management
- `TestContext`: Centralized state tracking across test execution
- Maintains active resources (experiments, models, runs, users) with workspace context
- Automatic cleanup lists with proper workspace switching
- Error capture for validation in failure scenarios

#### Action-Validation Separation
- **Action Functions**: Pure mutation of MLflow state, always set workspace context
- **Validation Functions**: Pure inspection of state, check both success and failure
- Both can be single functions or lists for complex multi-step workflows
- Composable and reusable across different test scenarios

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
- All MLflow operations include workspace context switching
- Cleanup respects workspace boundaries
- Baseline resources exist in each configured workspace

### Error Handling Strategy
- **Capture, don't fail**: Exceptions stored in `test_context.last_error` for validation
- **Retry with backoff**: Kubernetes operations use exponential backoff
- **Isolation**: Test failures don't prevent cleanup operations
- **Graceful degradation**: Missing resources logged but don't halt execution

---

## Contributors Guide

### Understanding the Test Framework

This framework uses a declarative approach where tests are defined as `TestData` objects that specify:
- User permissions to create
- Workspace to operate in
- Action to perform
- Validation to execute

#### Key Data Structures

**TestData** (`tests/shared/test_data.py`):
```python
@dataclass
class TestData:
    test_name: str                    # Descriptive test name
    user_info: UserInfo              # User role and permissions
    workspace_to_use: str            # Target workspace
    action_func: Callable            # Function to execute
    validate_func: Callable          # Function to validate result
```

**TestContext** (`tests/shared/test_context.py`):
```python
@dataclass
class TestContext:
    active_workspace: str            # Current workspace
    active_user: UserInfo           # Current authenticated user
    user_client: MlflowClient       # Authenticated MLflow client
    experiments_to_delete: dict     # experiment_id -> workspace
    models_to_delete: dict          # model_name -> workspace
    users_to_delete: list           # Users to clean up
    last_error: Exception           # Last error (for failure validation)
```

**UserInfo** (`tests/shared/user_info.py`):
```python
@dataclass
class UserInfo:
    workspace: str                   # User's assigned workspace
    role: UserRole                  # Permission level (READ/EDIT/USE/MANAGE)
    resource_type: ResourceType     # Resource scope (EXPERIMENTS/MODELS/etc)
    uname: str                      # Generated username
    password: str                   # Generated password (masked in logs)
```

### Adding Tests to Existing Test Files

#### Step 1: Define Test Scenarios

Add new `TestData` objects to the `test_scenarios` list in your test class:

```python
# In test_experiments.py or test_models.py
test_scenarios = [
    # Existing scenarios...

    TestData(
        test_name="Validate that MANAGE user can update experiment tags",
        user_info=UserInfo(
            workspace=Config.WORKSPACES[0],
            role=UserRole.MANAGE,
            resource_type=ResourceType.EXPERIMENTS
        ),
        workspace_to_use=Config.WORKSPACES[0],
        action_func=action_update_experiment_tags,     # You'll create this
        validate_func=validate_experiment_tags_updated, # You'll create this
    ),
    TestData(
        test_name="Validate that READ user cannot update experiment tags",
        user_info=UserInfo(
            workspace=Config.WORKSPACES[0],
            role=UserRole.READ,
            resource_type=ResourceType.EXPERIMENTS
        ),
        workspace_to_use=Config.WORKSPACES[0],
        action_func=action_update_experiment_tags,
        validate_func=validate_action_failed,          # Reuse existing validator
    ),
]
```

#### Step 2: Create Action Functions

Create new action functions in the appropriate `actions/` file:

```python
# In tests/actions/experiment_actions.py
def action_update_experiment_tags(test_context: TestContext) -> None:
    """Update tags on the active experiment."""
    if not test_context.active_experiment_id:
        raise ValueError("No active experiment to update tags on")

    # Set workspace context
    mlflow.set_workspace(test_context.active_workspace)

    tags = {"updated_by": test_context.active_user.uname, "test_tag": "test_value"}

    try:
        for key, value in tags.items():
            test_context.user_client.set_experiment_tag(
                test_context.active_experiment_id,
                key,
                value
            )
        logger.info(f"Successfully updated experiment {test_context.active_experiment_id} with tags: {tags}")

    except Exception as e:
        logger.error(f"Failed to update experiment tags: {e}")
        test_context.last_error = e
        raise
```

#### Step 3: Create Validation Functions

Create corresponding validation functions in the `validations/` directory:

```python
# In tests/validations/experiment_validations.py
def validate_experiment_tags_updated(test_context: TestContext) -> None:
    """Validate that experiment tags were successfully updated."""
    if test_context.last_error:
        raise AssertionError(f"Expected successful tag update but got error: {test_context.last_error}")

    if not test_context.active_experiment_id:
        raise AssertionError("No active experiment to validate tags on")

    # Verify tags exist
    mlflow.set_workspace(test_context.active_workspace)
    experiment = test_context.user_client.get_experiment(test_context.active_experiment_id)

    expected_tags = {"updated_by": test_context.active_user.uname, "test_tag": "test_value"}

    for key, expected_value in expected_tags.items():
        if key not in experiment.tags:
            raise AssertionError(f"Expected tag '{key}' not found in experiment tags")
        if experiment.tags[key] != expected_value:
            raise AssertionError(f"Tag '{key}' has value '{experiment.tags[key]}', expected '{expected_value}'")

    logger.info(f"Successfully validated experiment tags: {expected_tags}")
```

#### Step 4: Import and Run

Add imports to your test file:

```python
# In test_experiments.py
from .actions import (
    action_get_experiment,
    action_create_experiment,
    action_delete_experiment,
    action_update_experiment_tags,  # New import
)
from .validations.experiment_validations import (
    validate_experiment_retrieved,
    validate_experiment_created,
    validate_experiment_deleted,
    validate_experiment_tags_updated,  # New import
    validate_action_failed,
)
```

### Creating New Test Files

#### Step 1: Create Test File Structure

```python
# tests/test_new_feature.py
import logging
import mlflow
from mlflow import MlflowClient
from .shared import UserInfo, TestData
from .constants.config import Config
from .actions import (
    action_your_new_action,
)
from .validations.your_new_validations import (
    validate_your_new_validation,
    validate_action_failed,
)

import pytest
from mlflow_tests.enums import ResourceType, UserRole
from .base import TestBase

logger = logging.getLogger(__name__)

@pytest.mark.YourNewFeature
class TestYourNewFeature(TestBase):
    """Test your new feature with RBAC permissions."""

    test_scenarios = [
        TestData(
            test_name="Validate READ user can perform safe operations",
            user_info=UserInfo(
                workspace=Config.WORKSPACES[0],
                role=UserRole.READ,
                resource_type=ResourceType.EXPERIMENTS  # or appropriate resource
            ),
            workspace_to_use=Config.WORKSPACES[0],
            action_func=action_your_new_action,
            validate_func=validate_your_new_validation,
        ),
        # Add more scenarios...
    ]

    @pytest.mark.parametrize('test_data', test_scenarios, ids=lambda x: x.test_name)
    def test_your_new_feature(self, create_user_with_permissions, test_data: TestData):
        """Test your new feature operations with user permissions."""
        logger.info("=" * 80)
        logger.info(f"Starting test: {test_data.test_name}")
        logger.info(f"User role: {test_data.user_info.role.value}, Resource: {test_data.user_info.resource_type.value}")
        logger.info(f"Workspace: {test_data.workspace_to_use}")
        logger.info("=" * 80)

        # Create user with permissions
        user_info: UserInfo = create_user_with_permissions(
            workspace=test_data.user_info.workspace,
            user_role=test_data.user_info.role,
            resource_type=test_data.user_info.resource_type
        )

        # Set context
        self.test_context.active_user = user_info
        self.test_context.user_client = MlflowClient()
        self.test_context.active_workspace = test_data.workspace_to_use
        mlflow.set_workspace(self.test_context.active_workspace)

        # Execute action
        if test_data.action_func:
            try:
                test_data.action_func(self.test_context)
            except Exception as e:
                self.test_context.last_error = e

        # Validate result
        test_data.validate_func(self.test_context)
```

#### Step 2: Create Action Functions

```python
# tests/actions/your_new_actions.py
import logging
import mlflow
from ..shared.test_context import TestContext

logger = logging.getLogger(__name__)

def action_your_new_action(test_context: TestContext) -> None:
    """Perform your new MLflow operation."""
    mlflow.set_workspace(test_context.active_workspace)

    try:
        # Your MLflow operations here
        result = test_context.user_client.some_mlflow_operation()

        # Store result in context if needed
        # test_context.some_field = result

        logger.info("Successfully performed new action")

    except Exception as e:
        logger.error(f"Failed to perform new action: {e}")
        test_context.last_error = e
        raise
```

#### Step 3: Create Validation Functions

```python
# tests/validations/your_new_validations.py
import logging
from ..shared.test_context import TestContext

logger = logging.getLogger(__name__)

def validate_your_new_validation(test_context: TestContext) -> None:
    """Validate that your new operation succeeded."""
    if test_context.last_error:
        raise AssertionError(f"Expected success but got error: {test_context.last_error}")

    # Add specific validation logic
    # Check test_context fields, query MLflow, etc.

    logger.info("Successfully validated new operation")
```

### Best Practices

#### 1. Test Naming Convention
- Use descriptive names that clearly state the permission level, operation, and expected result
- Format: `"Validate that {ROLE} user {can/cannot} {operation} {additional_context}"`

#### 2. Action Functions
- Always set workspace context with `mlflow.set_workspace(test_context.active_workspace)`
- Store exceptions in `test_context.last_error` for failure validation
- Add relevant resources to cleanup lists using `test_context.add_*_for_cleanup()`
- Use comprehensive logging for debugging

#### 3. Validation Functions
- Check `test_context.last_error` first for failure scenarios
- Use specific assertion messages that explain what was expected vs actual
- Reuse `validate_action_failed()` for permission denial scenarios
- Add detailed logging for successful validations

#### 4. Resource Cleanup
- Always add created resources to cleanup lists:
  ```python
  test_context.add_experiment_for_cleanup(experiment_id, workspace)
  test_context.add_model_for_cleanup(model_name, workspace)
  test_context.add_user_for_cleanup(user_info)
  ```

#### 5. Workspace Isolation Testing
- Test cross-workspace permission failures by using different `workspace_to_use` vs `user_info.workspace`
- Validate that operations fail when user tries to access resources in other workspaces

#### 6. Permission Matrix Testing
- Test each operation with all relevant permission levels (READ, EDIT, USE, MANAGE)
- Include both success and failure scenarios
- Test workspace boundary violations

### Testing Your Changes

```bash
# Run your new tests
uv run pytest tests/test_your_new_feature.py -v

# Run with detailed logging
uv run pytest tests/test_your_new_feature.py -v -s --log-cli-level=INFO

# Run specific test scenarios
uv run pytest tests/test_your_new_feature.py::TestYourNewFeature::test_your_new_feature -k "READ user"
```

## Troubleshooting

### Common Issues and Solutions

#### Kubernetes Token Errors
**Problem**: Tests fail with authentication errors in K8s mode
**Solution**:
- Verify `K8_API_TOKEN` is valid and has sufficient permissions
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
- Verify all workspaces in `WORKSPACES` exist in MLflow server
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
- Verify `ARTIFACT_STORAGE` configuration is correct
- Ensure MLflow server has proper S3 backend configuration
- Check network connectivity to S3-compatible storage

### Debug Commands

```bash
# Run with maximum logging
uv run pytest -v -s --log-cli-level=DEBUG

# Run single test with detailed output
uv run pytest tests/test_experiments.py::TestExperiments::test_experiment -v -s --log-cli-level=INFO -k "specific_test_name"

# Check Kubernetes resources
kubectl get serviceaccounts -n test-namespace
kubectl get roles,rolebindings -n test-namespace

# Verify MLflow workspace access
curl -H "Authorization: Bearer $K8_API_TOKEN" "$MLFLOW_TRACKING_URI/api/2.0/mlflow/experiments/list"
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
- **kubernetes**: >=35.0.0 (Python client for Kubernetes API)
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