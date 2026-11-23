import ast
import os

ROOT = r"E:\matlab os"

TARGETS = {
    "matlab_os/intelligence/model_registry/registry.py": {
        "ModelRegistry": [
            "__init__", "_load", "list_models", "get_model",
            "register_model", "update_metadata", "create_profile",
            "assign_profile", "benchmark_history", "compare_models"
        ]
    },
    "matlab_os/intelligence/model_registry/hardening.py": {
        "HardenedModelRegistry": [
            "__init__", "benchmark_summary", "capability_suggestions"
        ]
    },
    "tests/intelligence/test_model_registry.py": {
        "ModelRegistryTests": [
            "test_profiles", "test_create_custom_profile",
            "test_model_registration", "test_benchmark_history"
        ]
    }
}

for relative, classes in TARGETS.items():
    path = os.path.join(ROOT, relative)

    print("\n" + "=" * 85)
    print("FILE:", relative)
    print("=" * 85)

    if not os.path.isfile(path):
        print("MISSING")
        continue

    with open(path, "r", encoding="utf-8-sig") as handle:
        source = handle.read()

    tree = ast.parse(source)

    for class_name, method_names in classes.items():
        class_node = next(
            (
                node for node in tree.body
                if isinstance(node, ast.ClassDef)
                and node.name == class_name
            ),
            None
        )

        if class_node is None:
            print("\nCLASS NOT FOUND:", class_name)
            continue

        for method_name in method_names:
            method = next(
                (
                    node for node in class_node.body
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and node.name == method_name
                ),
                None
            )

            if method is None:
                print("\nMETHOD NOT FOUND:", class_name + "." + method_name)
                continue

            print("\n" + "-" * 65)
            print(class_name + "." + method_name)
            print("-" * 65)
            print(ast.get_source_segment(source, method))

print("\nINSPECTION_COMPLETE")