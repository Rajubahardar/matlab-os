"""MATLAB Function Intelligence - Skill 16.

Discovers MATLAB capabilities, inspects functions and documentation,
generates controlled workflows, and verifies workspace outputs.

Function discovery does not grant execution permission. All generated
MATLAB code is dispatched through the existing MATLABAgent approval path.
"""

import re

SKILL_NAME = "MATLAB Function Intelligence"

OPERATIONS = {
    "discover_functions": "Discover MATLAB functions using which.",
    "search_documentation": "Search installed MATLAB documentation using lookfor.",
    "inspect_function": "Inspect function location, syntax and input/output counts.",
    "check_dependencies": "Inspect installed products and optional license features.",
    "find_simulink_blocks": "Find matching blocks in a Simulink library.",
    "recommend_function": "Find MATLAB functions related to an engineering task.",
    "generate_workflow": "Generate a sequence of MATLAB function calls without executing it.",
    "execute_workflow": "Generate a sequence of MATLAB function calls for approval-controlled execution.",
    "inspect_output": "Inspect a variable in the MATLAB base workspace.",
    "verify_output": "Check workspace output existence, type, dimensions and numeric validity."
}

_IDENTIFIER = re.compile(r"^[A-Za-z]\w*$")
_FUNCTION = re.compile(r"^[A-Za-z]\w*(?:\.[A-Za-z]\w*)*$")


def _identifier(value, label="name"):
    value = str(value or "").strip()
    if not _IDENTIFIER.fullmatch(value):
        raise ValueError("Invalid MATLAB " + label + ": " + value)
    return value


def _function_name(value):
    value = str(value or "").strip()
    if not _FUNCTION.fullmatch(value):
        raise ValueError("Invalid MATLAB function name: " + value)
    return value


def _matlab_string(value):
    """Create a MATLAB single-quoted string literal."""
    return "'" + str(value).replace("'", "''") + "'"


def _list(value, label):
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple)) or not value:
        raise ValueError(label + " must be a non-empty list.")
    return list(value)


def _workflow(params):
    steps = _list(params.get("steps"), "steps")
    lines = [
        "%% MATLAB Function Intelligence workflow",
        "disp('MATLAB Function Intelligence: workflow started');"
    ]

    for index, step in enumerate(steps, start=1):
        if not isinstance(step, dict):
            raise ValueError("Each workflow step must be a dictionary.")

        function = _function_name(step.get("function"))
        inputs = step.get("inputs", [])
        if not isinstance(inputs, (list, tuple)):
            raise ValueError("Workflow inputs must be a list of MATLAB variable names.")

        checked_inputs = [_identifier(x, "input variable") for x in inputs]
        output = step.get("output")

        if output:
            output = _identifier(output, "output variable")
            lines.append(
                "%s = %s(%s);" %
                (output, function, ", ".join(checked_inputs))
            )
        else:
            lines.append(
                "%s(%s);" % (function, ", ".join(checked_inputs))
            )

        lines.append("disp('Completed workflow step %d');" % index)

    lines.append("disp('MATLAB Function Intelligence: workflow finished');")
    return "\n".join(lines)


def build_code(operation, params=None):
    params = params or {}
    if not isinstance(params, dict):
        raise TypeError("Operation parameters must be a dictionary.")

    if operation not in OPERATIONS:
        raise ValueError("Unsupported MATLAB Function Intelligence operation: " + str(operation))

    if operation == "discover_functions":
        names = _list(params.get("function_names"), "function_names")
        names = [_function_name(x) for x in names]
        lines = ["disp('MATLAB function discovery');"]
        for name in names:
            lines.append(
                "fprintf('\\n%s: ', %s); disp(which(%s));" %
                ("%s", _matlab_string(name), _matlab_string(name))
            )
        return "\n".join(lines)

    if operation == "search_documentation":
        query = str(params.get("query", "")).strip()
        if not query:
            raise ValueError("query is required.")
        return (
            "disp('Searching installed MATLAB documentation...');\n"
            "lookfor(%s);\n" % _matlab_string(query)
        )

    if operation == "inspect_function":
        name = _function_name(params.get("function_name"))
        literal = _matlab_string(name)
        return (
            "mfi_name = %s;\n"
            "fprintf('Function: %%s\\n', mfi_name);\n"
            "fprintf('Location: %%s\\n', which(mfi_name));\n"
            "try, fprintf('Input count: %%d\\n', nargin(mfi_name)); "
            "catch ME, fprintf('Input count unavailable: %%s\\n', ME.message); end\n"
            "try, fprintf('Output count: %%d\\n', nargout(mfi_name)); "
            "catch ME, fprintf('Output count unavailable: %%s\\n', ME.message); end\n"
            "try, help(mfi_name); "
            "catch ME, fprintf('Help unavailable: %%s\\n', ME.message); end\n"
        ) % literal

    if operation == "check_dependencies":
        products = params.get("products", [])
        features = params.get("license_features", [])
        if not isinstance(products, (list, tuple)) or not isinstance(features, (list, tuple)):
            raise ValueError("products and license_features must be lists.")

        lines = [
            "disp('Installed MATLAB products:');",
            "mfi_products = ver;",
            "for mfi_i = 1:numel(mfi_products), "
            "fprintf('%s | %s\\n', mfi_products(mfi_i).Name, "
            "mfi_products(mfi_i).Version); end"
        ]

        for product in products:
            lines.append(
                "fprintf('Product search - %s: ', %s); "
                "disp(any(strcmpi({mfi_products.Name}, %s)));"
                % ("%s", _matlab_string(product), _matlab_string(product))
            )

        for feature in features:
            lines.append(
                "fprintf('License feature %s: ', %s); "
                "try, disp(license('test', %s)); "
                "catch ME, disp(ME.message); end"
                % ("%s", _matlab_string(feature), _matlab_string(feature))
            )
        return "\n".join(lines)

    if operation == "find_simulink_blocks":
        library = str(params.get("library", "simulink")).strip()
        query = str(params.get("block_query", "")).strip()
        if not library or not query:
            raise ValueError("library and block_query are required.")

        return (
            "mfi_library = %s;\n"
            "mfi_query = %s;\n"
            "load_system(mfi_library);\n"
            "mfi_blocks = find_system(mfi_library, "
            "'LookUnderMasks','all','FollowLinks','on',"
            "'RegExp','off','Name',mfi_query);\n"
            "disp(mfi_blocks);\n"
        ) % (_matlab_string(library), _matlab_string(query))

    if operation == "recommend_function":
        query = str(params.get("query", "")).strip()
        if not query:
            raise ValueError("query is required.")
        return (
            "disp('Candidate MATLAB functions from installed documentation:');\n"
            "lookfor(%s);\n"
            "disp('Review candidate documentation and toolbox availability before use.');\n"
        ) % _matlab_string(query)

    if operation in ("generate_workflow", "execute_workflow"):
        code = _workflow(params)
        if operation == "generate_workflow":
            return (
                "disp('Generated workflow preview. No function calls are executed by this operation.');\n"
                + "disp(" + _matlab_string(code) + ");\n"
            )
        return code

    if operation == "inspect_output":
        variable = _identifier(params.get("variable"), "workspace variable")
        literal = _matlab_string(variable)
        return (
            "mfi_variable = %s;\n"
            "mfi_info = whos(mfi_variable);\n"
            "if isempty(mfi_info)\n"
            "    fprintf('Variable %%s does not exist in the base workspace.\\n', mfi_variable);\n"
            "else\n"
            "    disp(mfi_info);\n"
            "    evalin('base', ['disp(' mfi_variable ')']);\n"
            "end\n"
        ) % literal

    if operation == "verify_output":
        variable = _identifier(params.get("variable"), "workspace variable")
        expected_size = params.get("expected_size")
        expected_class = params.get("expected_class")
        require_finite = bool(params.get("require_finite", True))

        lines = [
            "mfi_variable = %s;" % _matlab_string(variable),
            "mfi_info = whos(mfi_variable);",
            "mfi_exists = ~isempty(mfi_info);",
            "fprintf('Exists: %%d\\n', mfi_exists);",
            "if ~mfi_exists, error('MFI:MissingOutput','Expected output is missing.'); end",
            "fprintf('Class: %%s\\n', mfi_info.class);",
            "fprintf('Size: '); disp(mfi_info.size);",
        ]

        if expected_class:
            lines.extend([
                "if ~strcmp(class(evalin('base',mfi_variable)), %s), "
                "error('MFI:ClassMismatch','Unexpected output class.'); end"
                % _matlab_string(expected_class)
            ])

        if expected_size is not None:
            if not isinstance(expected_size, (list, tuple)) or not all(
                isinstance(x, int) and x > 0 for x in expected_size
            ):
                raise ValueError("expected_size must contain positive integers.")
            size_literal = "[" + " ".join(str(x) for x in expected_size) + "]"
            lines.append(
                "if ~isequal(size(evalin('base',mfi_variable)), %s), "
                "error('MFI:SizeMismatch','Unexpected output dimensions.'); end"
                % size_literal
            )

        if require_finite:
            lines.append(
                "mfi_value = evalin('base',mfi_variable);\n"
                "if isnumeric(mfi_value) && any(~isfinite(mfi_value(:))), "
                "error('MFI:NonFinite','Output contains NaN or Inf.'); end"
            )

        lines.append("disp('Output verification passed.');")
        return "\n".join(lines)

    raise ValueError("Operation not implemented: " + str(operation))
