"""features/ không được import hay mở đường dẫn tới thư mục nhãn."""

import ast
from pathlib import Path

FEATURES = Path(__file__).resolve().parents[1] / "features"


def test_features_do_not_touch_truth_paths() -> None:
    offenders = []
    for path in FEATURES.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and "truth" in node.module:
                offenders.append(f"{path.name}: import {node.module}")
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if "truth" in alias.name:
                        offenders.append(f"{path.name}: import {alias.name}")
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and "truth" in node.value.replace("\\", "/").lower():
                offenders.append(f"{path.name}: {node.value!r}")
    assert not offenders, "features/ đụng dữ liệu nhãn:\n" + "\n".join(offenders)
