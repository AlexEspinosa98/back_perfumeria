import html
import re

_VAR = re.compile(r"\{\{\s*(\w+)\s*\}\}")


def render(template: str, values: dict, *, escape: bool = False) -> str:
    """Reemplaza {{variable}}. Con escape=True (HTML) los valores se escapan; las URLs *_url no."""

    def sub(m: re.Match) -> str:
        key = m.group(1)
        val = str(values.get(key, ""))
        return val if (not escape or key.endswith("_url")) else html.escape(val)

    return _VAR.sub(sub, template)
