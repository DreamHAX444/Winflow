import re
from typing import Any

from winflow.engine.execution_context import ExecutionContext

# Match {{ namespace.key }} or {{ key }}
VARIABLE_PATTERN = re.compile(r"\{\{\s*([a-zA-Z0-9_.]+)\s*\}\}")


class VariableResolver:
    """Safely resolves runtime variables without using eval or exec."""

    @staticmethod
    def resolve_string(value: str, context: ExecutionContext) -> str:
        """Resolve variables in a single string."""
        if not isinstance(value, str):
            return value

        def replacer(match: re.Match) -> str:
            path = match.group(1).strip()
            resolved = VariableResolver._get_value_from_path(path, context)
            if resolved is None:
                return match.group(0)  # leave unresolved
            return str(resolved)

        # Fast path if the string is EXACTLY one variable (allows returning non-string types)
        # e.g. value == "{{ my_int }}" -> returns int instead of string "5"
        exact_match = re.fullmatch(r"\{\{\s*([a-zA-Z0-9_.]+)\s*\}\}", value)
        if exact_match:
            path = exact_match.group(1).strip()
            resolved = VariableResolver._get_value_from_path(path, context)
            if resolved is not None:
                return resolved

        return VARIABLE_PATTERN.sub(replacer, value)

    @staticmethod
    def resolve(data: Any, context: ExecutionContext) -> Any:
        """Recursively resolve variables in a data structure (dict, list, str)."""
        if isinstance(data, str):
            return VariableResolver.resolve_string(data, context)
        elif isinstance(data, dict):
            return {k: VariableResolver.resolve(v, context) for k, v in data.items()}
        elif isinstance(data, list):
            return [VariableResolver.resolve(v, context) for v in data]
        return data

    @staticmethod
    def _get_value_from_path(path: str, context: ExecutionContext) -> Any:
        parts = path.split('.')
        namespace = parts[0]
        
        # Determine the root object based on namespace
        root = None
        if namespace == "variables":
            root = context.variables
            parts = parts[1:]
        elif namespace == "trigger_event":
            root = context.trigger_event or {}
            parts = parts[1:]
        elif namespace == "execution":
            root = {
                "id": context.execution_id,
                "step": context.current_step,
                "start_time": context.start_time
            }
            parts = parts[1:]
        elif namespace == "loop":
            root = {
                "iteration": context.loop_iteration,
                "index": context.variables.get("_loop_index", 0),
                "item": context.variables.get("_loop_item", None)
            }
            parts = parts[1:]
        else:
            # Default to variables namespace if no explicit namespace is given
            root = context.variables
        
        # Traverse the path
        current = root
        for part in parts:
            if isinstance(current, dict):
                current = current.get(part)
            elif hasattr(current, part):
                current = getattr(current, part)
            else:
                return None
                
            if current is None:
                break
                
        return current
