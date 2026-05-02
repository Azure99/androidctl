"""Shared boundary-model conventions for androidctld schemas."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Literal, cast

from pydantic import BaseModel, ConfigDict, ValidationError
from pydantic_core import InitErrorDetails
from typing_extensions import Self

if TYPE_CHECKING:
    from pydantic._internal._model_construction import ModelMetaclass as _ModelMetaclass
else:
    _ModelMetaclass = type(BaseModel)


def to_camel(name: str) -> str:
    parts = name.split("_")
    if len(parts) == 1:
        return name
    head, *tail = parts
    return head + "".join(
        segment[:1].upper() + segment[1:] for segment in tail if segment
    )


class _ApiModelMeta(_ModelMetaclass):
    def __call__(cls, *args: Any, **kwargs: Any) -> Any:
        model_cls = cast(type[ApiModel], cls)
        if kwargs and model_cls._contains_alias_kwargs(kwargs):
            raise ValidationError.from_exception_data(
                model_cls.__name__,
                model_cls._alias_init_errors(kwargs),
            )
        return super().__call__(*args, **kwargs)


class ApiModel(BaseModel, metaclass=_ApiModelMeta):
    model_config = ConfigDict(
        strict=True,
        extra="forbid",
        alias_generator=to_camel,
        validate_by_alias=True,
        validate_by_name=True,
        use_enum_values=False,
    )

    @classmethod
    def model_validate(
        cls,
        obj: Any,
        *,
        strict: bool | None = None,
        extra: Literal["allow", "ignore", "forbid"] | None = None,
        from_attributes: bool | None = None,
        context: Any | None = None,
        by_alias: bool | None = None,
        by_name: bool | None = None,
    ) -> Self:
        # External payload parsing stays alias-only by default, while internal
        # code can still use normal snake_case keyword construction.
        return super().model_validate(
            obj,
            strict=strict,
            extra=extra,
            from_attributes=from_attributes,
            context=context,
            by_alias=True if by_alias is None else by_alias,
            by_name=False if by_name is None else by_name,
        )

    @classmethod
    def model_validate_json(
        cls,
        json_data: str | bytes | bytearray,
        *,
        strict: bool | None = None,
        extra: Literal["allow", "ignore", "forbid"] | None = None,
        context: Any | None = None,
        by_alias: bool | None = None,
        by_name: bool | None = None,
    ) -> Self:
        return super().model_validate_json(
            json_data,
            strict=strict,
            extra=extra,
            context=context,
            by_alias=True if by_alias is None else by_alias,
            by_name=False if by_name is None else by_name,
        )

    def model_copy(
        self,
        *,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        model_type = type(self)
        if update and model_type._contains_alias_kwargs(update):
            raise ValidationError.from_exception_data(
                model_type.__name__,
                model_type._alias_init_errors(update),
            )
        return super().model_copy(update=update, deep=deep)

    @classmethod
    def _contains_alias_kwargs(cls, data: Mapping[str, Any]) -> bool:
        return any(
            field.alias is not None
            and field.alias != field_name
            and field.alias in data
            for field_name, field in cls.model_fields.items()
        )

    @classmethod
    def _alias_init_errors(cls, data: Mapping[str, Any]) -> list[InitErrorDetails]:
        return [
            InitErrorDetails(
                type="extra_forbidden",
                loc=(field.alias,),
                input=data[field.alias],
            )
            for field_name, field in cls.model_fields.items()
            if field.alias is not None
            and field.alias != field_name
            and field.alias in data
        ]


def dump_api_model(model: ApiModel) -> dict[str, Any]:
    return model.model_dump(by_alias=True, mode="json")
