import os
import importlib
import inspect
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, get_type_hints

from omniagents.core.debug import Debug
from omniagents.core.session.manager import Session as _SessionType
from agents.strict_schema import ensure_strict_json_schema


def server_function(
    func: Callable | None = None,
    *,
    name: Optional[str] = None,
    name_override: Optional[str] = None,
    description: Optional[str] = None,
    params_schema: Optional[dict] = None,
    result_schema: Optional[dict] = None,
    strict: bool = True,
) -> Callable:
    def _jsonify(obj: Any) -> Any:
        import dataclasses

        if obj is None:
            return None
        if isinstance(obj, (str, int, float, bool)):
            return obj
        if isinstance(obj, (list, tuple, set)):
            return [_jsonify(x) for x in obj]
        if isinstance(obj, dict):
            return {str(k): _jsonify(v) for k, v in obj.items()}
        if dataclasses.is_dataclass(obj):
            return _jsonify(dataclasses.asdict(obj))
        if hasattr(obj, "model_dump"):
            try:
                return _jsonify(obj.model_dump())
            except Exception:
                pass
        if hasattr(obj, "dict"):
            try:
                return _jsonify(obj.dict())
            except Exception:
                pass
        try:
            import json as _json

            _json.dumps(obj)
            return obj
        except Exception:
            return str(obj)

    def decorator(f: Callable) -> Callable:
        fn_name = name_override or name or f.__name__

        sig = inspect.signature(f)
        hints = get_type_hints(f)
        params = list(sig.parameters.items())
        takes_session = False
        filtered_params: list[tuple[str, inspect.Parameter]] = []
        if params:
            first_name, first_param = params[0]
            ann = hints.get(first_name, first_param.annotation)
            if ann is not inspect._empty and (ann is _SessionType):
                takes_session = True
            else:
                filtered_params.append((first_name, first_param))
            for name_, p in params[1:]:
                filtered_params.append((name_, p))
        else:
            filtered_params = []

        Model = None
        payload_names: list[str] = []
        auto_params_schema = None
        if params_schema is None:
            try:
                from pydantic import BaseModel, Field, create_model
                from pydantic.fields import FieldInfo

                fields: dict[str, Any] = {}
                for name_, p in filtered_params:
                    payload_names.append(name_)
                    ann = hints.get(name_, p.annotation)
                    if ann is inspect._empty:
                        ann = Any
                    default = p.default
                    if p.kind == p.VAR_POSITIONAL:
                        ann = list[Any]
                        fields[name_] = (ann, Field(default_factory=list))
                    elif p.kind == p.VAR_KEYWORD:
                        ann = dict[str, Any]
                        fields[name_] = (ann, Field(default_factory=dict))
                    else:
                        if default is inspect._empty:
                            fields[name_] = (ann, Field(...))
                        elif isinstance(default, FieldInfo):
                            fields[name_] = (ann, default)
                        else:
                            fields[name_] = (ann, Field(default=default))
                if fields:
                    Model = create_model(
                        f"{fn_name}_args", __base__=BaseModel, **fields
                    )
                    schema = Model.model_json_schema()
                else:
                    schema = {
                        "type": "object",
                        "properties": {},
                        "required": [],
                        "additionalProperties": False,
                    }
                auto_params_schema = (
                    ensure_strict_json_schema(schema) if strict else schema
                )
            except Exception:
                auto_params_schema = None

        auto_result_schema = None
        if result_schema is None:
            try:
                from pydantic import TypeAdapter

                ret_ann = sig.return_annotation
                if ret_ann is not inspect._empty:
                    auto_result_schema = TypeAdapter(ret_ann).json_schema()
            except Exception:
                auto_result_schema = None

        async def _on_invoke(service, session, args: Optional[dict] = None):
            if takes_session and session is None:
                raise ValueError(f"Session is required for '{fn_name}'")
            data = args or {}
            if strict and payload_names:
                extra_keys = set(data.keys()) - set(payload_names)
                if extra_keys:
                    raise ValueError(f"Unexpected parameters: {sorted(extra_keys)}")
            if Model is not None:
                try:
                    parsed = Model(**data)
                except Exception as e:
                    raise ValueError(f"Invalid params for '{fn_name}': {e}")
                kwargs = {n: getattr(parsed, n, None) for n in payload_names}
            else:
                kwargs = dict(data)
            if inspect.iscoroutinefunction(f):
                if takes_session:
                    res = await f(session, **kwargs)
                else:
                    res = await f(**kwargs)
            else:
                if takes_session:
                    res = f(session, **kwargs)
                else:
                    res = f(**kwargs)
            return _jsonify(res)

        setattr(f, "_is_omniagents_server_function", True)
        setattr(f, "_server_function_name", str(fn_name))
        if description is not None:
            setattr(f, "_server_function_description", description)
        if params_schema is not None:
            setattr(
                f,
                "_server_function_params_schema",
                (
                    params_schema
                    if not strict
                    else ensure_strict_json_schema(params_schema)
                ),
            )
        elif auto_params_schema is not None:
            setattr(f, "_server_function_params_schema", auto_params_schema)
        if result_schema is not None:
            setattr(f, "_server_function_result_schema", result_schema)
        elif auto_result_schema is not None:
            setattr(f, "_server_function_result_schema", auto_result_schema)
        setattr(f, "_server_function_on_invoke", _on_invoke)
        setattr(f, "_server_function_strict", bool(strict))
        return f

    if func is None:
        return decorator
    return decorator(func)


def discover_server_functions_in_dir(
    base_dir: str = "server_functions",
) -> Dict[str, dict]:
    discovered: Dict[str, dict] = {}
    base_path = Path(base_dir).resolve()

    if not base_path.is_dir():
        if Debug.enabled():
            print(
                f"Warning: Server function directory '{base_dir}' not found or not a directory."
            )
        return discovered

    if Debug.enabled():
        print(f"Starting server function discovery in directory: {base_path}")

    parent_path = str(base_path.parent)
    base_path_str = str(base_path)
    original_sys_path = sys.path[:]

    if parent_path not in sys.path:
        sys.path.insert(0, parent_path)
    if base_path_str not in sys.path:
        sys.path.insert(0, base_path_str)

    try:
        for filepath in base_path.rglob("*.py"):
            if filepath.name == "__init__.py":
                continue
            try:
                relative_path = filepath.relative_to(base_path.parent)
                module_path = str(relative_path.with_suffix(""))
                module_path = module_path.replace(os.sep, ".")
            except Exception:
                if Debug.enabled():
                    print(
                        f"Warning: Skipping file {filepath} outside expected parent {base_path.parent}"
                    )
                continue

            try:
                module = importlib.import_module(module_path)
            except Exception as e:
                print(
                    f"ERROR: Could not import module '{module_path}' from '{filepath}': {e}"
                )
                continue

            for name_, obj in inspect.getmembers(module):
                if getattr(obj, "_is_omniagents_server_function", False):
                    fn_name = getattr(obj, "_server_function_name", name_)
                    meta = {
                        "func": obj,
                        "name": fn_name,
                        "description": getattr(
                            obj, "_server_function_description", None
                        ),
                        "params_schema": getattr(
                            obj, "_server_function_params_schema", None
                        ),
                        "result_schema": getattr(
                            obj, "_server_function_result_schema", None
                        ),
                        "on_invoke": getattr(obj, "_server_function_on_invoke", None),
                        "strict": getattr(obj, "_server_function_strict", None),
                    }
                    if fn_name in discovered and Debug.enabled():
                        print(
                            f"Warning: Duplicate server function name '{fn_name}' in {module_path}; overwriting."
                        )
                    discovered[fn_name] = meta

    finally:
        sys.path = original_sys_path

    if Debug.enabled():
        print(
            f"Completed server function discovery: Found {len(discovered)} functions in '{base_dir}'."
        )
    return discovered


def build_server_functions_registrar(
    functions: List[Callable] | Dict[str, Callable] | Dict[str, dict],
):
    def _extract(name_key: str, value: Any) -> tuple[
        str,
        Callable,
        Optional[str],
        Optional[dict],
        Optional[dict],
        Optional[Callable],
        Optional[bool],
    ]:
        if isinstance(value, dict) and "func" in value:
            return (
                value.get("name") or name_key,
                value["func"],
                value.get("description"),
                value.get("params_schema"),
                value.get("result_schema"),
                value.get("on_invoke"),
                value.get("strict"),
            )
        if callable(value):
            n = (
                getattr(value, "_server_function_name", None)
                or name_key
                or value.__name__
            )
            desc = getattr(value, "_server_function_description", None)
            ps = getattr(value, "_server_function_params_schema", None)
            rs = getattr(value, "_server_function_result_schema", None)
            oi = getattr(value, "_server_function_on_invoke", None)
            st = getattr(value, "_server_function_strict", None)
            return (n, value, desc, ps, rs, oi, st)
        raise TypeError("Unsupported server function value type")

    items: List[
        tuple[
            str,
            Callable,
            Optional[str],
            Optional[dict],
            Optional[dict],
            Optional[Callable],
            Optional[bool],
        ]
    ] = []
    if isinstance(functions, dict):
        for k, v in functions.items():
            items.append(_extract(k, v))
    else:
        for fn in functions:
            items.append(_extract("", fn))

    def registrar(service: Any) -> None:
        for name_, fn, desc, ps, rs, oi, st in items:
            try:
                service.register_server_function(
                    name_,
                    fn,
                    description=desc,
                    params_schema=ps,
                    result_schema=rs,
                    on_invoke=oi,
                    strict=st,
                )
            except Exception as e:
                print(f"Warning: Failed to register server function '{name_}': {e}")

    return registrar


def make_server_functions_registrar_from_dir(
    base_dir: str = "server_functions", names: Optional[List[str]] = None
):
    discovered = discover_server_functions_in_dir(base_dir)
    selected: Dict[str, dict]
    if names is None:
        selected = discovered
    else:
        selected = {}
        missing: List[str] = []
        name_set = set(names)
        for n in name_set:
            meta = discovered.get(n)
            if meta:
                selected[n] = meta
            else:
                missing.append(n)
        if missing:
            print(
                f"Warning: The following server functions were not found in '{base_dir}': {', '.join(missing)}"
            )
    return build_server_functions_registrar(selected)


def register_server_functions_from_dir(
    service: Any, base_dir: str = "server_functions", names: Optional[List[str]] = None
) -> None:
    registrar = make_server_functions_registrar_from_dir(base_dir, names)
    registrar(service)
