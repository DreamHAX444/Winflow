from typing import Any

from winflow.core.variables import VariableResolver
from winflow.engine.execution_context import ExecutionContext


class ConditionEvaluator:
    """Evaluates conditional expressions using runtime variables."""

    @classmethod
    def evaluate(cls, condition: dict[str, Any], context: ExecutionContext) -> bool:
        """Evaluate a condition dictionary."""
        if not condition:
            return True

        group_type = str(condition.get("type", "")).upper()
        if group_type:
            children = condition.get("conditions", [])
            if not isinstance(children, list):
                raise ValueError("Condition group 'conditions' must be a list.")
            if group_type == "ALL":
                return all(cls.evaluate(child, context) for child in children)
            if group_type == "ANY":
                return any(cls.evaluate(child, context) for child in children)
            if group_type == "NOT":
                return not any(cls.evaluate(child, context) for child in children)
            raise ValueError(f"Unsupported condition group: {group_type}")

        if "all" in condition:
            return all(cls.evaluate(c, context) for c in condition["all"])
        
        if "any" in condition:
            return any(cls.evaluate(c, context) for c in condition["any"])
            
        if "not" in condition:
            return not cls.evaluate(condition["not"], context)

        operator = condition.get("operator")
        if not operator:
            raise ValueError(f"Missing operator in condition: {condition}")

        left_raw = condition.get("left")
        right_raw = condition.get("right")

        left = VariableResolver.resolve(left_raw, context)
        right = VariableResolver.resolve(right_raw, context)

        operator = {
            "greater_than": "gt",
            "greater_or_equal": "gte",
            "less_than": "lt",
            "less_or_equal": "lte",
        }.get(operator, operator)

        if operator == "equals":
            return left == right
        elif operator == "not_equals":
            return left != right
        elif operator == "contains":
            if left is None or right is None:
                return False
            return str(right) in str(left)
        elif operator == "not_contains":
            if left is None or right is None:
                return True
            return str(right) not in str(left)
        elif operator == "starts_with":
            return left is not None and right is not None and str(left).startswith(str(right))
        elif operator == "ends_with":
            return left is not None and right is not None and str(left).endswith(str(right))
        elif operator == "exists":
            return left is not None
        elif operator == "not_exists":
            return left is None
        elif operator == "is_empty":
            if left is None:
                return True
            if isinstance(left, (str, list, dict, set, tuple)):
                return len(left) == 0
            return False
        elif operator == "is_not_empty":
            if left is None:
                return False
            if isinstance(left, (str, list, dict, set, tuple)):
                return len(left) > 0
            return True
        
        # Numeric comparisons
        if operator in ("gt", "gte", "lt", "lte"):
            try:
                l_num = float(left) if left is not None else 0.0
                r_num = float(right) if right is not None else 0.0
            except (ValueError, TypeError):
                return False
                
            if operator == "gt":
                return l_num > r_num
            elif operator == "gte":
                return l_num >= r_num
            elif operator == "lt":
                return l_num < r_num
            elif operator == "lte":
                return l_num <= r_num

        raise ValueError(f"Unsupported operator: {operator}")
