from __future__ import annotations

from enum import Enum

import pytest
from pydantic import Field, TypeAdapter, ValidationError

from androidctld.schema.base import ApiModel, dump_api_model
from androidctld.schema.persistence import RuntimeStateFile
from androidctld.schema.validation_errors import (
    validation_error_field_path,
    validation_error_to_bad_request,
    validation_error_to_device_bootstrap_error,
    validation_error_to_schema_decode_error,
)


class ExampleModel(ApiModel):
    my_value: int = 1
    flag: bool


class NestedModel(ApiModel):
    value: int


class ContainerModel(ApiModel):
    nested: NestedModel


class ConstrainedModel(ApiModel):
    count: int = Field(ge=2)
    title: str = Field(min_length=1)


class SiblingContainerModel(ApiModel):
    first: NestedModel
    second: NestedModel


def test_api_model_rejects_string_booleans() -> None:
    with pytest.raises(ValidationError):
        ExampleModel.model_validate({"flag": "false"})


def test_api_model_dumps_camel_case_aliases() -> None:
    model = ExampleModel.model_construct(my_value=1)
    assert model.model_dump(by_alias=True) == {"myValue": 1}


def test_dump_api_model_uses_alias_json_mode() -> None:
    class Example(ApiModel):
        my_value: tuple[int, int]
        flag: bool

    payload = dump_api_model(Example(my_value=(1, 2), flag=True))

    assert payload == {"myValue": [1, 2], "flag": True}


def test_api_model_rejects_snake_case_external_keys() -> None:
    with pytest.raises(ValidationError):
        ExampleModel.model_validate({"my_value": 1, "flag": True})


def test_api_model_internal_snake_case_construct_still_works() -> None:
    model = ExampleModel.model_construct(my_value=1)
    assert model.my_value == 1


def test_api_model_rejects_alias_kwargs_in_python_construction() -> None:
    with pytest.raises(ValidationError) as error:
        ExampleModel(myValue=1, flag=True)

    issues = error.value.errors(include_url=False)
    assert issues[0]["type"] == "extra_forbidden"
    assert issues[0]["loc"] == ("myValue",)


def test_api_model_rejects_alias_keys_in_model_copy_update() -> None:
    model = ExampleModel(my_value=1, flag=True)

    with pytest.raises(ValidationError) as error:
        model.model_copy(update={"myValue": 2})

    issues = error.value.errors(include_url=False)
    assert issues[0]["type"] == "extra_forbidden"
    assert issues[0]["loc"] == ("myValue",)


def test_api_model_boundary_relies_on_dump_helper_not_json_wrapper_methods() -> None:
    model = ExampleModel.model_construct(my_value=1, flag=True)

    assert not hasattr(ApiModel, "to_json")
    assert not hasattr(ApiModel, "from_json")
    assert not hasattr(model, "to_json")
    assert not hasattr(model, "from_json")
    assert dump_api_model(model) == {"myValue": 1, "flag": True}


def test_validation_error_field_path_formats_nested_list_locations() -> None:
    assert (
        validation_error_field_path(("result", "events", 3, "data", 2, "id"))
        == "result.events[3].data[2].id"
    )


def test_validation_error_field_path_uses_root_for_empty_location() -> None:
    assert validation_error_field_path(()) == "root"


def test_validation_error_to_bad_request_prefixes_root_list_indices() -> None:
    with pytest.raises(ValidationError) as error:
        TypeAdapter(list[int]).validate_python(["x"])

    daemon_error = validation_error_to_bad_request(error.value, field_name="result")

    assert daemon_error.message == "result[0] must be an integer"
    assert daemon_error.details == {"field": "result[0]"}


def test_validation_error_to_bad_request_prefixes_root_type_errors() -> None:
    with pytest.raises(ValidationError) as error:
        TypeAdapter(list[int]).validate_python("x")

    daemon_error = validation_error_to_bad_request(error.value, field_name="result")

    assert daemon_error.message == "result must be a list"
    assert daemon_error.details == {"field": "result"}


def test_validation_error_to_bad_request_normalizes_boolean_errors() -> None:
    with pytest.raises(ValidationError) as error:
        ExampleModel.model_validate({"myValue": 1, "flag": "false"})

    daemon_error = validation_error_to_bad_request(error.value)

    assert daemon_error.message == "flag must be a boolean"
    assert daemon_error.details == {"field": "flag"}


def test_validation_error_to_bad_request_reports_top_level_unknown_fields() -> None:
    with pytest.raises(ValidationError) as error:
        ExampleModel.model_validate({"myValue": 1, "flag": True, "extra": 2})

    daemon_error = validation_error_to_bad_request(
        error.value,
        field_name="result",
    )

    assert daemon_error.message == "result has unsupported fields"
    assert daemon_error.details == {
        "field": "result",
        "unknownFields": ["extra"],
    }


def test_validation_error_to_bad_request_normalizes_integer_constraints() -> None:
    with pytest.raises(ValidationError) as error:
        ConstrainedModel.model_validate({"count": 1, "title": "ok"})

    daemon_error = validation_error_to_bad_request(error.value)

    assert daemon_error.message == "count must be an integer >= 2"
    assert daemon_error.details == {"field": "count"}


def test_validation_error_to_bad_request_prefers_shallowest_container() -> None:
    with pytest.raises(ValidationError) as error:
        ContainerModel.model_validate(
            {
                "nested": {"value": 1, "innerExtra": 2},
                "outerExtra": 3,
                "anotherOuterExtra": 4,
            }
        )

    daemon_error = validation_error_to_bad_request(
        error.value,
        field_name="result",
    )

    assert daemon_error.message == "result has unsupported fields"
    assert daemon_error.details == {
        "field": "result",
        "unknownFields": ["anotherOuterExtra", "outerExtra"],
    }


def test_schema_decode_error_groups_unsupported_fields_at_container() -> None:
    with pytest.raises(ValidationError) as error:
        ContainerModel.model_validate(
            {
                "nested": {"value": 1, "innerExtra": 2},
                "outerExtra": 3,
            }
        )

    schema_error = validation_error_to_schema_decode_error(
        error.value,
        field_name="result",
    )

    assert schema_error.field == "result"
    assert schema_error.problem == "has unsupported fields"


def test_validation_error_to_bad_request_prefers_first_sibling_container() -> None:
    with pytest.raises(ValidationError) as error:
        SiblingContainerModel.model_validate(
            {
                "first": {"value": 1, "extraA": 2},
                "second": {"value": 2, "extraB": 3},
            }
        )

    daemon_error = validation_error_to_bad_request(
        error.value,
        field_name="result",
    )

    assert daemon_error.message == "result.first has unsupported fields"
    assert daemon_error.details == {
        "field": "result.first",
        "unknownFields": ["extraA"],
    }


def test_validation_error_to_device_bootstrap_error_groups_unknown_fields() -> None:
    with pytest.raises(ValidationError) as error:
        ContainerModel.model_validate(
            {
                "nested": {"value": 1, "innerExtra": 2},
                "outerExtra": 3,
                "anotherOuterExtra": 4,
            }
        )

    device_error = validation_error_to_device_bootstrap_error(
        error.value,
        field_name="result",
    )

    assert device_error.message == "device RPC result has unsupported fields"
    assert device_error.details == {
        "field": "result",
        "reason": "invalid_payload",
        "unknownFields": ["anotherOuterExtra", "outerExtra"],
    }


def test_validation_error_to_device_bootstrap_error_normalizes_boolean_errors() -> None:
    with pytest.raises(ValidationError) as error:
        ExampleModel.model_validate({"myValue": 1, "flag": "false"})

    device_error = validation_error_to_device_bootstrap_error(error.value)

    assert device_error.message == "device RPC flag must be a boolean"
    assert device_error.details == {"field": "flag", "reason": "invalid_payload"}


def test_schema_decode_error_normalizes_string_constraints() -> None:
    with pytest.raises(ValidationError) as error:
        ConstrainedModel.model_validate({"count": 2, "title": ""})

    schema_error = validation_error_to_schema_decode_error(error.value)

    assert schema_error.field == "title"
    assert schema_error.problem == "must be a non-empty string"


def test_validation_error_to_schema_decode_error_prefixes_field_name() -> None:
    with pytest.raises(ValidationError) as error:
        ExampleModel.model_validate({"myValue": 1, "flag": "false"})

    schema_error = validation_error_to_schema_decode_error(
        error.value,
        field_name="result",
    )

    assert schema_error.field == "result.flag"
    assert schema_error.problem == "must be a boolean"


def test_schema_error_normalizes_unsupported_runtime_status_on_runtime_state() -> None:
    with pytest.raises(ValidationError) as error:
        RuntimeStateFile.model_validate(
            {
                "status": "bogus",
                "updatedAt": "2026-04-28T00:00:00Z",
            }
        )

    schema_error = validation_error_to_schema_decode_error(
        error.value,
        field_name="runtime",
    )

    assert schema_error.field == "runtime.status"
    assert schema_error.problem == "must be a supported runtime status"


def test_schema_error_does_not_special_case_non_runtime_session_status_name() -> None:
    legacy_status_enum = Enum("Session" "Status", {"READY": "ready"}, type=str)

    class LegacyStatusContainer(ApiModel):
        status: legacy_status_enum

    with pytest.raises(ValidationError) as error:
        LegacyStatusContainer.model_validate({"status": "broken"})

    schema_error = validation_error_to_schema_decode_error(error.value)

    assert schema_error.field == "status"
    assert schema_error.problem == "is invalid"


def test_validation_error_to_schema_decode_error_uses_root_for_model_errors() -> None:
    with pytest.raises(ValidationError) as error:
        ExampleModel.model_validate(None)

    schema_error = validation_error_to_schema_decode_error(error.value)

    assert schema_error.field == "root"
    assert schema_error.problem == "must be a JSON object"
