import ast
from pathlib import Path

root = Path(r"E:\matlab os")

targets = {
    "matlab_os/intelligence/model_registry/registry.py": {
        "ModelRegistry": [
            "list_models",
            "discover_runtime_models",
            "synchronize",
            "health_check",
        ],
    },
    "matlab_os/intelligence/model_registry/runtime_adapter.py": {
        "OllamaRuntimeAdapter": [
            "list_models",
            "show_model",
            "is_available",
        ],
    },
}

for relative, classes in targets.items():
    path = root / relative
    source = path.read_text(encoding="utf-8-sig")
    tree = ast.parse(source)

    print("\n" + "=" * 80)
    print(relative)
    print("=" * 80)

    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name in classes:
            print("\nCLASS:", node.name)

            wanted = classes[node.name]

            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if child.name in wanted:
                        print("\n--- METHOD:", child.name, "---")
                        print(ast.get_source_segment(source, child))

print("\nRUNTIME_CONTRACT_INSPECTION=PASS")