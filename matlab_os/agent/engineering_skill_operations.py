"""Dedicated operation builders for engineering skills 3–10."""

import math
import numbers
import re

OPERATIONS = {
    "Power Systems Engineering": [
        "per_unit", "three_phase_fault"
    ],
    "Control Systems Engineering": [
        "state_space_analysis"
    ],
    "Simscape Electrical": [
        "audit_blocks", "inspect_component"
    ],
    "Simulation and Analysis": [
        "signal_metrics", "frequency_metrics"
    ],
    "Optimization and Parameter Tuning": [
        "parameter_sweep"
    ],
    "Engineering Verification and Validation": [
        "validate_signal", "compare_signals"
    ],
    "Research Paper Reproduction": [
        "compare_reference_metrics"
    ],
    "MATLAB Project Management and Documentation": [
        "project_inventory"
    ],
}

def number(value, name):
    if isinstance(value, bool) or not isinstance(value, numbers.Real):
        raise ValueError(name + " must be numeric.")
    if not math.isfinite(float(value)):
        raise ValueError(name + " must be finite.")
    return repr(float(value))

def vector(values, name, minimum=1):
    if not isinstance(values, (list, tuple)) or len(values) < minimum:
        raise ValueError(name + " must contain at least " + str(minimum) + " values.")
    return "[" + " ".join(number(v, name) for v in values) + "]"

def matrix(values, name):
    if not isinstance(values, (list, tuple)) or not values:
        raise ValueError(name + " must be a non-empty matrix.")
    width = None
    rows = []
    for row in values:
        if not isinstance(row, (list, tuple)) or not row:
            raise ValueError(name + " contains an invalid row.")
        if width is None:
            width = len(row)
        if len(row) != width:
            raise ValueError(name + " must be rectangular.")
        rows.append(" ".join(number(v, name) for v in row))
    return "[" + "; ".join(rows) + "]"

def identifier(value, name):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z]\w*", value):
        raise ValueError("Invalid MATLAB " + name + ".")
    return value

def matlab_string(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(name + " must be a non-empty string.")
    if any(ord(c) < 32 for c in value):
        raise ValueError(name + " contains control characters.")
    return "'" + value.replace("'", "''") + "'"

def signal_pair(p, minimum=4):
    t = p.get("time")
    x = p.get("signal")
    if not isinstance(t, (list, tuple)) or not isinstance(x, (list, tuple)):
        raise ValueError("time and signal must be numeric lists.")
    if len(t) != len(x) or len(t) < minimum:
        raise ValueError("time and signal must have equal lengths and enough samples.")
    if any(not isinstance(v, numbers.Real) or isinstance(v, bool) or
           not math.isfinite(float(v)) for v in t):
        raise ValueError("time contains invalid values.")
    if any(float(t[i]) >= float(t[i+1]) for i in range(len(t)-1)):
        raise ValueError("time must be strictly increasing.")
    return vector(t, "time", minimum), vector(x, "signal", minimum)

def build_operation(skill, operation, p):
    if skill not in OPERATIONS or operation not in OPERATIONS[skill]:
        raise ValueError("Unsupported engineering skill operation.")

    # 3. Power Systems Engineering
    if skill == "Power Systems Engineering":
        if operation == "per_unit":
            s = number(p.get("S_base_MVA"), "S_base_MVA")
            v = number(p.get("V_base_kV"), "V_base_kV")
            return f"""
S_base_MVA = {s};
V_base_kV = {v};
assert(S_base_MVA > 0 && V_base_kV > 0);
Z_base_ohm = V_base_kV^2/S_base_MVA;
I_base_kA = S_base_MVA/(sqrt(3)*V_base_kV);
fprintf('Zbase = %.8g ohm; Ibase = %.8g kA\\n',Z_base_ohm,I_base_kA);
"""
        if operation == "three_phase_fault":
            v = number(p.get("V_ll_kV"), "V_ll_kV")
            z = number(p.get("Z_th_ohm"), "Z_th_ohm")
            return f"""
V_ll_kV = {v}; Z_th_ohm = {z};
assert(V_ll_kV > 0 && Z_th_ohm > 0);
I_sc_kA = V_ll_kV/(sqrt(3)*Z_th_ohm);
fprintf('Three-phase short-circuit current = %.8g kA\\n',I_sc_kA);
"""

    # 4. Control Systems Engineering
    if skill == "Control Systems Engineering":
        A = matrix(p.get("A"), "A")
        B = matrix(p.get("B"), "B")
        C = matrix(p.get("C"), "C")
        D = matrix(p.get("D"), "D")
        return f"""
A = {A}; B = {B}; C = {C}; D = {D};
assert(size(A,1)==size(A,2),'A must be square');
assert(size(B,1)==size(A,1),'B dimension mismatch');
assert(size(C,2)==size(A,2),'C dimension mismatch');
assert(size(D,1)==size(C,1) && size(D,2)==size(B,2),'D dimension mismatch');
sys = ss(A,B,C,D);
poles = eig(A);
disp('STATE-SPACE MODEL'); disp(sys);
disp('OPEN-LOOP POLES'); disp(poles);
"""

    # 5. Simscape Electrical
    if skill == "Simscape Electrical":
        model = identifier(p.get("model"), "model name")
        if operation == "audit_blocks":
            blocks = p.get("blocks", [])
            if not isinstance(blocks, (list, tuple)):
                raise ValueError("blocks must be a list.")
            block_text = "{" + ",".join(
                matlab_string(b, "block name") for b in blocks
            ) + "}"
            return f"""
modelName = '{model}';
load_system(modelName);
requiredBlocks = {block_text};
fprintf('SIMSCAPE_AUDIT_BEGIN: %s\\n',modelName);
for k=1:numel(requiredBlocks)
    found = find_system(modelName,'LookUnderMasks','all', ...
        'FollowLinks','on','Name',requiredBlocks{{k}});
    fprintf('%s: %d match(es)\\n',requiredBlocks{{k}},numel(found));
end
fprintf('SIMSCAPE_AUDIT_END\\n');
"""
        component = matlab_string(p.get("component"), "component")
        return f"""
modelName = '{model}';
load_system(modelName);
componentName = {component};
matches = find_system(modelName,'LookUnderMasks','all', ...
    'FollowLinks','on','Name',componentName);
if isempty(matches)
    error('Component not found: %s',componentName);
end
for k=1:numel(matches)
    fprintf('COMPONENT: %s\\n',matches{{k}});
    disp(get_param(matches{{k}},'ObjectParameters'));
end
"""

    # 6. Simulation and Analysis
    if skill == "Simulation and Analysis":
        t, x = signal_pair(p)
        if operation == "signal_metrics":
            return f"""
t = {t}; x = {x};
assert(all(isfinite(t)) && all(isfinite(x)));
metrics.sample_count = numel(x);
metrics.mean = mean(x);
metrics.rms = sqrt(mean(x.^2));
metrics.peak = max(abs(x));
metrics.minimum = min(x);
metrics.maximum = max(x);
metrics.peak_to_peak = max(x)-min(x);
metrics.average_sample_rate = 1/mean(diff(t));
disp(metrics);
"""
        return f"""
t = {t}; x = {x};
assert(all(isfinite(t)) && all(isfinite(x)));
x = x-mean(x);
crossings = find(x(1:end-1)<=0 & x(2:end)>0);
assert(numel(crossings)>=2,'Insufficient positive-going crossings');
crossingTimes = zeros(size(crossings));
for k=1:numel(crossings)
    i = crossings(k);
    crossingTimes(k) = t(i)-x(i)*(t(i+1)-t(i))/(x(i+1)-x(i));
end
periods = diff(crossingTimes);
frequency_hz = 1/mean(periods);
frequency_std_hz = std(1./periods);
fprintf('Estimated frequency = %.8g Hz\\n',frequency_hz);
fprintf('Cycle frequency standard deviation = %.8g Hz\\n',frequency_std_hz);
"""

    # 7. Optimization and Parameter Tuning
    if skill == "Optimization and Parameter Tuning":
        model = identifier(p.get("model"), "model name")
        block = matlab_string(p.get("block_path"), "block path")
        parameter = p.get("parameter")
        allowed = {
            "Gain", "Value", "Amplitude", "Frequency",
            "Resistance", "Inductance", "Capacitance",
            "P", "Q", "Vdc"
        }
        if parameter not in allowed:
            raise ValueError("Parameter is not in the permitted sweep list.")
        values = vector(p.get("values"), "values")
        return f"""
modelName = '{model}';
blockPath = {block};
parameterName = '{parameter}';
sweepValues = {values};
load_system(modelName);
originalValue = get_param(blockPath,parameterName);
restoreValue = onCleanup(@() set_param(blockPath,parameterName,originalValue));
sweepResults = cell(numel(sweepValues),1);
for k=1:numel(sweepValues)
    set_param(blockPath,parameterName,num2str(sweepValues(k),17));
    sweepResults{{k}} = sim(modelName);
    fprintf('SWEEP_POINT %d/%d VALUE=%g\\n',k,numel(sweepValues),sweepValues(k));
end
clear restoreValue;
disp('PARAMETER_SWEEP_COMPLETE');
"""

    # 8. Engineering Verification and Validation
    if skill == "Engineering Verification and Validation":
        t, x = signal_pair(p)
        if operation == "validate_signal":
            minimum = p.get("minimum_samples", 4)
            if not isinstance(minimum, int) or isinstance(minimum, bool) or minimum < 4:
                raise ValueError("minimum_samples must be an integer >= 4.")
            return f"""
t = {t}; x = {x};
validation.finite = all(isfinite(t)) && all(isfinite(x));
validation.time_monotonic = all(diff(t)>0);
validation.sample_count = numel(x);
validation.minimum_samples = {minimum};
validation.sample_count_valid = numel(x)>=validation.minimum_samples;
validation.rms = sqrt(mean(x.^2));
validation.peak = max(abs(x));
validation.passed = validation.finite && validation.time_monotonic && ...
    validation.sample_count_valid;
disp(validation);
assert(validation.passed,'SIGNAL_VALIDATION_FAILED');
"""
        rt, rx = signal_pair({
            "time": p.get("reference_time"),
            "signal": p.get("reference_signal")
        })
        return f"""
t = {t}; measured = {x};
referenceTime = {rt}; reference = {rx};
referenceAligned = interp1(referenceTime,reference,t,'linear',NaN);
valid = isfinite(referenceAligned) & isfinite(measured);
assert(any(valid),'No overlapping valid samples');
errorSignal = measured(valid)-referenceAligned(valid);
comparison.rmse = sqrt(mean(errorSignal.^2));
comparison.mae = mean(abs(errorSignal));
comparison.max_absolute_error = max(abs(errorSignal));
comparison.measured_rms = sqrt(mean(measured(valid).^2));
comparison.reference_rms = sqrt(mean(referenceAligned(valid).^2));
disp(comparison);
"""

    # 9. Research Paper Reproduction
    if skill == "Research Paper Reproduction":
        expected = p.get("reference_metrics")
        measured = p.get("measured_metrics")
        if not isinstance(expected, dict) or not isinstance(measured, dict):
            raise ValueError("reference_metrics and measured_metrics must be dictionaries.")
        if not expected or set(expected) != set(measured):
            raise ValueError("Reference and measured metric names must match.")
        names = list(expected)
        rows = []
        for name in names:
            if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", name):
                raise ValueError("Invalid metric name.")
            rows.append(
                f"reference.{name} = {number(expected[name], name)}; "
                f"measured.{name} = {number(measured[name], name)};"
            )
        return "\n".join([
            "reproduction = struct();",
            *rows,
            "metricNames = fieldnames(reference);",
            "for k=1:numel(metricNames)",
            "    n = metricNames{k};",
            "    reproduction.(n).reference = reference.(n);",
            "    reproduction.(n).measured = measured.(n);",
            "    reproduction.(n).absolute_error = abs(measured.(n)-reference.(n));",
            "    reproduction.(n).relative_error = reproduction.(n).absolute_error/max(abs(reference.(n)),eps);",
            "end",
            "disp(reproduction);"
        ])

    # 10. MATLAB Project Management and Documentation
    if skill == "MATLAB Project Management and Documentation":
        root = matlab_string(p.get("project_root"), "project_root")
        return f"""
projectRoot = {root};
assert(isfolder(projectRoot),'Project folder does not exist');
scripts = dir(fullfile(projectRoot,'**','*.m'));
models = dir(fullfile(projectRoot,'**','*.slx'));
dataFiles = dir(fullfile(projectRoot,'**','*.mat'));
projectReport.root = projectRoot;
projectReport.matlab_version = version;
projectReport.release = version('-release');
projectReport.script_count = numel(scripts);
projectReport.model_count = numel(models);
projectReport.mat_file_count = numel(dataFiles);
projectReport.generated_at = datetime('now');
disp(projectReport);
"""

    raise ValueError("Operation implementation missing.")
