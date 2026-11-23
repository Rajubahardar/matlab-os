
"""Declarative Simulink engineering model builder."""

import re
from pathlib import Path


class SimulinkModelBuilder:

    @staticmethod
    def quote(value):
        return "'" + str(value).replace("'", "''") + "'"

    @staticmethod
    def identifier(value, label):
        value = str(value).strip()
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", value):
            raise ValueError("Invalid " + label + ": " + value)
        return value

    @classmethod
    def validate(cls, spec):
        if not isinstance(spec, dict):
            raise ValueError("Specification must be a dictionary.")

        model = cls.identifier(spec.get("model", ""), "model name")
        blocks = spec.get("blocks")
        connections = spec.get("connections", [])

        if not isinstance(blocks, list) or not blocks:
            raise ValueError("At least one block is required.")

        if not isinstance(connections, list):
            raise ValueError("Connections must be a list.")

        names = set()

        for block in blocks:
            if not isinstance(block, dict):
                raise ValueError("Each block must be a dictionary.")

            name = cls.identifier(block.get("name", ""), "block name")
            if name in names:
                raise ValueError("Duplicate block: " + name)
            names.add(name)

            library = block.get("library")
            if not isinstance(library, str) or not library.strip():
                raise ValueError("Missing library path for " + name)

            parameters = block.get("parameters", {})
            if not isinstance(parameters, dict):
                raise ValueError("Block parameters must be a dictionary.")

            for key, value in parameters.items():
                if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", str(key)):
                    raise ValueError("Invalid parameter name: " + str(key))
                if isinstance(value, (dict, list, tuple)):
                    raise ValueError("Parameter values must be scalar.")

        for connection in connections:
            if not isinstance(connection, dict):
                raise ValueError("Invalid connection.")

            for field in ("from", "to"):
                endpoint = str(connection.get(field, ""))
                match = re.fullmatch(
                    r"([A-Za-z][A-Za-z0-9_]*)/(\d+)", endpoint
                )
                if not match or match.group(1) not in names:
                    raise ValueError("Invalid endpoint: " + endpoint)

        stop = str(spec.get("stop_time", "10"))
        if not re.fullmatch(r"\d+(?:\.\d*)?(?:[eE][+-]?\d+)?", stop):
            raise ValueError("Invalid simulation stop time.")
        if float(stop) <= 0:
            raise ValueError("Stop time must be positive.")

        return model, blocks, connections

    @classmethod
    def generate_script(cls, spec, output_dir):
        model, blocks, connections = cls.validate(spec)
        q = cls.quote

        output_dir = str(Path(output_dir).resolve())
        model_file = str(Path(output_dir) / (model + ".slx"))
        results_file = str(Path(output_dir) / (model + "_results.mat"))
        report_file = str(Path(output_dir) / (model + "_report.txt"))

        lines = [
            "model = " + q(model) + ";",
            "outDir = " + q(output_dir) + ";",
            "if ~exist(outDir,'dir'), mkdir(outDir); end",
            "modelFile = " + q(model_file) + ";",
            "resultsFile = " + q(results_file) + ";",
            "reportFile = " + q(report_file) + ";",
            "if bdIsLoaded(model), close_system(model,0); end",
            "new_system(model);",
            "open_system(model);",
            "try",
        ]

        for block in blocks:
            name = block["name"]
            destination = model + "/" + name
            position = block.get("position", [100, 100, 150, 150])

            if (
                not isinstance(position, (list, tuple))
                or len(position) != 4
                or any(
                    isinstance(x, bool) or not isinstance(x, (int, float))
                    for x in position
                )
            ):
                raise ValueError("Invalid position for " + name)

            position_text = "[" + " ".join(map(str, position)) + "]"

            lines.append(
                "add_block(" + q(block["library"]) + ","
                + q(destination) + ",'Position'," + position_text + ");"
            )

            for parameter, value in block.get("parameters", {}).items():
                lines.append(
                    "set_param(" + q(destination) + ","
                    + q(parameter) + "," + q(value) + ");"
                )

        for connection in connections:
            lines.append(
                "add_line(model,"
                + q(connection["from"]) + ","
                + q(connection["to"])
                + ",'autorouting','on');"
            )

        lines.append(
            "set_param(model,'StopTime',"
            + q(spec.get("stop_time", "10")) + ");"
        )

        if spec.get("solver"):
            solver = str(spec["solver"])
            if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", solver):
                raise ValueError("Invalid solver name.")
            lines.append(
                "set_param(model,'Solver'," + q(solver) + ");"
            )

        lines.extend([
            "set_param(model,'SimulationCommand','update');",
            "save_system(model,modelFile);",
            "simOut = sim(model,'ReturnWorkspaceOutputs','on');",
            "save(resultsFile,'simOut');",
            "fid = fopen(reportFile,'w');",
            "if fid < 0, error('Cannot write report file.'); end",
            "fprintf(fid,'SIMULINK BUILD REPORT\\n');",
            "fprintf(fid,'Model: %s\\n',model);",
            "fprintf(fid,'Blocks: %d\\n'," + str(len(blocks)) + ");",
            "fprintf(fid,'Connections: %d\\n'," + str(len(connections)) + ");",
            "fprintf(fid,'Simulation: completed\\n');",
            "fprintf(fid,'Model file: %s\\n',modelFile);",
            "fprintf(fid,'Results: %s\\n',resultsFile);",
            "fclose(fid);",
            "% --- Numerical result extraction ---",
            "try",
            "    t_end = simOut.tout(end);",
            "    y_end = simOut.yout(end);",
            "    tau = 0.01;",
            "    y_ref = 1.0 - exp(-t_end / tau);",
            "    err_abs = abs(y_end - y_ref);",
            "    is_pass = (err_abs < 0.001);",
            "    fprintf('RESULT_JSON: %s\\n', jsonencode(struct('t_end',t_end,'y_end',y_end,'y_ref',y_ref,'err_abs',err_abs,'pass',is_pass)));",
            "catch",
            "    % Sinks other than To Workspace may not yield array yout",
            "end",
            "disp('SIMULINK_BUILD_SUCCESS');",
            "disp(modelFile);",
            "disp(resultsFile);",
            "disp(reportFile);",
            "catch ME",
            "    disp(getReport(ME,'extended'));",
            "    rethrow(ME);",
            "end",
        ])

        return "\n".join(lines)

    @classmethod
    def execute(cls, agent, spec):
        model, _, _ = cls.validate(spec)
        output_dir = Path(spec.get("output_dir", "models"))
        output_dir.mkdir(parents=True, exist_ok=True)

        code = cls.generate_script(spec, output_dir)

        return agent.execute_matlab_code_interactive(
            code,
            reason="Build, validate and simulate Simulink model: " + model,
        )
