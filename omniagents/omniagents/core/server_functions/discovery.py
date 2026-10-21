import os
import importlib
import inspect
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, get_type_hints

from omniagents.core.debug import Debug
from omniagents.core.session.manager import Session as _SessionType
from agents.strict_schema import ensure_strict_json_schema
from omniagents.rpc.protocol_catalog import ExecutionRequirement, ResourceScope


def _validate_execution_descriptor(
    *,
    function_name: str,
    resource_scope: Any,
    execution_requirements: Any,
) -> tuple[ResourceScope, ExecutionRequirement]:
    """Validate one server function's mandatory execution descriptor.

    Server functions are dynamically dispatched behind the single
    ``server_call`` protocol operation, so missing or loosely coerced metadata
    would bypass the typed protocol catalog's fail-closed classification.
    Require the actual enum types here rather than accepting their wire values.
    """
    if not isinstance(resource_scope, ResourceScope):
        raise TypeError(
            f"Server function '{function_name}' must declare resource_scope "
            "as a ResourceScope"
        )
    if not isinstance(execution_requirements, ExecutionRequirement):
        raise TypeError(
            f"Server function '{function_name}' must declare "
            "execution_requirements as an ExecutionRequirement"
        )
    return resource_scope, execution_requirements


def server_function(
    func: Callable | None = None,
    *,
    resource_scope: ResourceScope,
    execution_requirements: ExecutionRequirement,
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
        if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
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
        validated_scope, validated_requirements = _validate_execution_descriptor(
            function_name=str(fn_name),
            resource_scope=resource_scope,
            execution_requirements=execution_requirements,
        )

        sig = inspect.signature(f)
        hints = get_type_hints(f)
        params = list(sig.parameters.items())
        takes_service = False
        takes_session = False
        filtered_params: list[tuple[str, inspect.Parameter]] = []
        idx = 0
        if params:
            first_name, first_param = params[0]
            if first_name == "service":
                takes_service = True
                idx = 1

        if len(params) > idx:
            name_, p = params[idx]
            ann = hints.get(name_, p.annotation)
            if ann is not inspect._empty and (ann is _SessionType):
                takes_session = True
                idx += 1

        for name_, p in params[idx:]:
            filtered_params.append((name_, p))

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
                if takes_service and takes_session:
                    res = await f(service, session, **kwargs)
                elif takes_service:
                    res = await f(service, **kwargs)
                elif takes_session:
                    res = await f(session, **kwargs)
                else:
                    res = await f(**kwargs)
            else:
                if takes_service and takes_session:
                    res = f(service, session, **kwargs)
                elif takes_service:
                    res = f(service, **kwargs)
                elif takes_session:
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
        setattr(f, "_server_function_resource_scope", validated_scope)
        setattr(
            f,
            "_server_function_execution_requirements",
            validated_requirements,
        )
        return f

    if func is None:
        return decorator
    return decorator(func)


def discover_server_functions_in_dir(
    base_dir: str = "server_functions",
) -> Dict[str, dict]:
    """Discover ``@server_function``-decorated callables under *base_dir*.

    Modules are loaded under their full canonical dotted name when the
    directory is a real Python package (so ``omniagents/builtin/
    server_functions/`` and a project's local ``server_functions/`` don't
    collide on ``sys.modules['server_functions']``); directories without
    an ``__init__.py`` chain, or whose canonical name is already bound to
    a different path, fall back to a unique synthetic prefix. Modules are
    never evicted afterward — server-function modules hold live state
    (tick loops, provider installs) and must stay process singletons.
    Mirrors ``omniagents.core.tools.discovery.discover_functions_in_dir``.
    """
    import hashlib
    import importlib.util
    import types

    from omniagents.core.tools.discovery import _resolve_canonical_package_name

    discovered: Dict[str, dict] = {}
    base_path = Path(base_dir).resolve()

    if not base_path.is_dir():
        Debug.log(
            f"Warning: Server function directory '{base_dir}' not found or not a directory."
        )
        return discovered

    Debug.log(f"Starting server function discovery in directory: {base_path}")

    canonical_name = _resolve_canonical_package_name(base_path)
    canonical_init = (base_path / "__init__.py").resolve() if canonical_name else None
    use_synthetic = canonical_name is None
    if canonical_name and not use_synthetic:
        existing = sys.modules.get(canonical_name)
        if existing is not None:
            existing_file = getattr(existing, "__file__", None)
            if existing_file:
                try:
                    if Path(existing_file).resolve() != canonical_init:
                        use_synthetic = True
                except OSError:
                    use_synthetic = True
            else:
                use_synthetic = True

    synth_pkg: Optional[str] = None
    if use_synthetic:
        synth_pkg = (
            "_omniagents_sf_disco_"
            + hashlib.md5(str(base_path).encode()).hexdigest()[:12]
        )
        if synth_pkg not in sys.modules:
            pkg = types.ModuleType(synth_pkg)
            pkg.__path__ = [str(base_path)]
            pkg.__package__ = synth_pkg
            sys.modules[synth_pkg] = pkg
        module_prefix = synth_pkg
    else:
        assert canonical_name is not None  # use_synthetic covers the None case
        module_prefix = canonical_name

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
                relative_path = filepath.relative_to(base_path)
                sub_name = ".".join(relative_path.with_suffix("").parts)
                module_path = f"{module_prefix}.{sub_name}"
            except Exception:
                Debug.log(
                    f"Warning: Skipping file {filepath} outside expected parent {base_path.parent}"
                )
                continue

            try:
                if use_synthetic:
                    if module_path in sys.modules:
                        module = sys.modules[module_path]
                    else:
                        spec = importlib.util.spec_from_file_location(
                            module_path, str(filepath)
                        )
                        if spec is None or spec.loader is None:
                            raise ImportError(
                                f"could not build import spec for {filepath}"
                            )
                        module = importlib.util.module_from_spec(spec)
                        module.__package__ = synth_pkg
                        sys.modules[module_path] = module
                        spec.loader.exec_module(module)
                else:
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
                        "resource_scope": getattr(
                            obj, "_server_function_resource_scope", None
                        ),
                        "execution_requirements": getattr(
                            obj, "_server_function_execution_requirements", None
                        ),
                    }
                    _validate_execution_descriptor(
                        function_name=str(fn_name),
                        resource_scope=meta["resource_scope"],
                        execution_requirements=meta["execution_requirements"],
                    )
                    Debug.log(
                        f"Warning: Duplicate server function name '{fn_name}' in {module_path}; overwriting."
                    )
                    discovered[fn_name] = meta

    finally:
        sys.path = original_sys_path

    Debug.log(
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
        ResourceScope,
        ExecutionRequirement,
    ]:
        if isinstance(value, dict) and "func" in value:
            resolved_name = value.get("name") or name_key
            scope, requirements = _validate_execution_descriptor(
                function_name=str(resolved_name),
                resource_scope=value.get("resource_scope"),
                execution_requirements=value.get("execution_requirements"),
            )
            return (
                resolved_name,
                value["func"],
                value.get("description"),
                value.get("params_schema"),
                value.get("result_schema"),
                value.get("on_invoke"),
                value.get("strict"),
                scope,
                requirements,
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
            scope, requirements = _validate_execution_descriptor(
                function_name=str(n),
                resource_scope=getattr(value, "_server_function_resource_scope", None),
                execution_requirements=getattr(
                    value, "_server_function_execution_requirements", None
                ),
            )
            return (n, value, desc, ps, rs, oi, st, scope, requirements)
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
            ResourceScope,
            ExecutionRequirement,
        ]
    ] = []
    if isinstance(functions, dict):
        for k, v in functions.items():
            items.append(_extract(k, v))
    else:
        for fn in functions:
            items.append(_extract("", fn))

    def registrar(service: Any) -> None:
        for name_, fn, desc, ps, rs, oi, st, scope, requirements in items:
            try:
                service.register_server_function(
                    name_,
                    fn,
                    description=desc,
                    params_schema=ps,
                    result_schema=rs,
                    on_invoke=oi,
                    strict=st,
                    resource_scope=scope,
                    execution_requirements=requirements,
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
