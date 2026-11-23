import math

SKILLS = {
    "Electrical Machines Engineering": {
        "machine_parameters": "Machine speed, slip and rotor frequency",
        "equivalent_circuit": "Induction machine equivalent circuit"
    },
    "Power Electronics Engineering": {
        "buck_converter": "Buck converter calculations",
        "pwm_analysis": "PWM timing calculations"
    },
    "Renewable Energy and Microgrids": {
        "pv_array": "PV array calculations",
        "energy_assessment": "Renewable energy assessment"
    },
    "Power Quality and Harmonics": {
        "harmonic_analysis": "Harmonic analysis and THD",
        "power_quality": "Signal power quality indicators"
    },
    "Engineering Data Acquisition and Processing": {
        "import_data": "Import numerical data",
        "clean_data": "Clean numerical data",
        "resample_data": "Resample numerical data",
        "extract_features": "Extract signal features"
    }
}

def _num(p, key, default, minimum=None, maximum=None):
    v = p.get(key, default)
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise ValueError(key + " must be numeric")
    if not math.isfinite(v):
        raise ValueError(key + " must be finite")
    if minimum is not None and v < minimum:
        raise ValueError(key + " is below its allowed minimum")
    if maximum is not None and v > maximum:
        raise ValueError(key + " exceeds its allowed maximum")
    return format(float(v), ".15g")

def _var(p, key, default):
    v = p.get(key, default)
    if not isinstance(v, str) or not v.isidentifier() or v.startswith("__"):
        raise ValueError("Invalid MATLAB variable name: " + key)
    return v

def _string(v):
    if not isinstance(v, str) or not v.strip():
        raise ValueError("A non-empty string is required")
    if "\x00" in v or "\n" in v or "\r" in v:
        raise ValueError("Invalid string")
    return "'" + v.replace("'", "''") + "'"

def _signal(p, minimum=2):
    x = p.get("signal", [0, 1, 0, -1, 0, 1, 0, -1])
    if not isinstance(x, (list, tuple)) or len(x) < minimum:
        raise ValueError("Signal has too few samples")
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in x):
        raise ValueError("Signal must contain finite numeric samples")
    return " ".join(format(float(v), ".15g") for v in x)

def build_code(skill_name, operation, params=None):
    p = params or {}
    if not isinstance(p, dict):
        raise ValueError("params must be a dictionary")

    if skill_name == "Electrical Machines Engineering":
        if operation == "machine_parameters":
            f = _num(p, "frequency", 50, 0.000001)
            poles = _num(p, "poles", 4, 1)
            rpm = _num(p, "rotor_speed_rpm", 1440, 0)
            return f"""
frequency_hz = {f};
number_of_poles = {poles};
rotor_speed_rpm = {rpm};
synchronous_speed_rpm = 120*frequency_hz/number_of_poles;
slip = (synchronous_speed_rpm-rotor_speed_rpm)/synchronous_speed_rpm;
rotor_frequency_hz = abs(slip)*frequency_hz;
machine_results = struct('synchronous_speed_rpm',synchronous_speed_rpm, ...
    'slip',slip,'rotor_frequency_hz',rotor_frequency_hz);
disp(machine_results);
"""
        if operation == "equivalent_circuit":
            r1 = _num(p, "R1", 0.5, 0)
            x1 = _num(p, "X1", 1.2, 0)
            r2 = _num(p, "R2", 0.3, 0)
            x2 = _num(p, "X2", 1.0, 0)
            xm = _num(p, "Xm", 25, 0.000001)
            s = _num(p, "slip", 0.03, 0.000001, 1)
            return f"""
R1={r1}; X1={x1}; R2={r2}; X2={x2}; Xm={xm}; slip={s};
Z1=R1+1i*X1; Z2=R2/slip+1i*X2; Zm=1i*Xm;
Zparallel=(Zm*Z2)/(Zm+Z2);
Zinput=Z1+Zparallel;
machine_equivalent_results=struct('Z1',Z1,'Z2',Z2, ...
    'Zm',Zm,'Zinput',Zinput,'magnitude',abs(Zinput));
disp(machine_equivalent_results);
"""

    if skill_name == "Power Electronics Engineering":
        if operation == "buck_converter":
            vin = _num(p, "input_voltage", 100, 0.000001)
            vo = _num(p, "output_voltage", 50, 0.000001)
            fs = _num(p, "switching_frequency", 20000, 0.000001)
            inductance = _num(p, "inductance", 0.001, 0.000000001)
            if float(vo) > float(vin):
                raise ValueError("Buck output voltage cannot exceed input voltage")
            return f"""
Vin={vin}; Vout={vo}; fs={fs}; L={inductance};
duty_ratio=Vout/Vin;
switching_period=1/fs;
inductor_ripple_current=Vout*(1-duty_ratio)/(L*fs);
buck_results=struct('duty_ratio',duty_ratio, ...
    'switching_period_s',switching_period, ...
    'inductor_ripple_current_A',inductor_ripple_current);
disp(buck_results);
"""
        if operation == "pwm_analysis":
            fs = _num(p, "switching_frequency", 10000, 0.000001)
            duty = _num(p, "duty_cycle", 50, 0, 100)
            return f"""
switching_frequency_hz={fs}; duty_cycle_percent={duty};
switching_period_s=1/switching_frequency_hz;
on_time_s=switching_period_s*duty_cycle_percent/100;
off_time_s=switching_period_s-on_time_s;
pwm_results=struct('frequency_hz',switching_frequency_hz, ...
    'period_s',switching_period_s,'on_time_s',on_time_s, ...
    'off_time_s',off_time_s);
disp(pwm_results);
"""

    if skill_name == "Renewable Energy and Microgrids":
        if operation == "pv_array":
            vm = _num(p, "module_voltage", 40, 0.000001)
            im = _num(p, "module_current", 10, 0)
            ns = _num(p, "series_modules", 10, 1)
            np = _num(p, "parallel_strings", 2, 1)
            return f"""
module_voltage={vm}; module_current={im};
series_modules={ns}; parallel_strings={np};
array_voltage=module_voltage*series_modules;
array_current=module_current*parallel_strings;
array_power=array_voltage*array_current;
pv_results=struct('voltage_V',array_voltage, ...
    'current_A',array_current,'power_W',array_power);
disp(pv_results);
"""
        if operation == "energy_assessment":
            power = _num(p, "rated_power_kw", 10, 0.000001)
            hours = _num(p, "daily_sun_hours", 5, 0)
            days = _num(p, "days", 30, 0.000001)
            pr = _num(p, "performance_ratio", 0.8, 0, 1)
            return f"""
rated_power_kw={power}; daily_sun_hours={hours};
days={days}; performance_ratio={pr};
daily_energy_kwh=rated_power_kw*daily_sun_hours*performance_ratio;
period_energy_kwh=daily_energy_kwh*days;
capacity_factor=daily_energy_kwh/(rated_power_kw*24);
energy_results=struct('daily_energy_kwh',daily_energy_kwh, ...
    'period_energy_kwh',period_energy_kwh, ...
    'capacity_factor',capacity_factor);
disp(energy_results);
"""

    if skill_name == "Power Quality and Harmonics":
        if operation == "harmonic_analysis":
            x = _signal(p, 4)
            fs = _num(p, "sampling_frequency", 1000, 0.000001)
            f1 = _num(p, "fundamental_frequency", 50, 0.000001)
            hmax = int(_num(p, "max_harmonic", 10, 1, 100))
            return f"""
x=[{x}]; x=x(:);
fs={fs}; f1={f1}; max_harmonic={hmax};
N=length(x); X=fft(x-mean(x)); freq=(0:N-1)'*fs/N;
harmonic_magnitudes=zeros(max_harmonic,1);
for h=1:max_harmonic
    [~,idx]=min(abs(freq-h*f1));
    harmonic_magnitudes(h)=2*abs(X(idx))/N;
end
if harmonic_magnitudes(1)<=eps
    error('Fundamental component is too small for THD.');
end
thd_percent=100*sqrt(sum(harmonic_magnitudes(2:end).^2))/ ...
    harmonic_magnitudes(1);
harmonic_results=struct('harmonic_magnitudes',harmonic_magnitudes, ...
    'thd_percent',thd_percent);
disp(harmonic_results);
"""
        if operation == "power_quality":
            x = _signal(p, 2)
            return f"""
x=[{x}]; x=x(:);
signal_rms=sqrt(mean(x.^2));
signal_peak=max(abs(x));
signal_mean=mean(x);
if signal_rms<=eps, crest_factor=NaN;
else, crest_factor=signal_peak/signal_rms; end
power_quality_results=struct('rms',signal_rms, ...
    'peak',signal_peak,'mean',signal_mean, ...
    'crest_factor',crest_factor);
disp(power_quality_results);
"""

    if skill_name == "Engineering Data Acquisition and Processing":
        if operation == "import_data":
            path = _string(p.get("file_path", "data.csv"))
            out = _var(p, "output_variable", "acquired_data")
            return f"""
data_file={path};
{out}=readmatrix(data_file);
if isempty({out}), error('Imported dataset is empty.'); end
fprintf('Imported %d rows and %d columns.\\n', ...
    size({out},1),size({out},2));
"""
        if operation == "clean_data":
            var = _var(p, "variable", "acquired_data")
            out = _var(p, "output_variable", "cleaned_data")
            method = p.get("method", "linear")
            if method not in ("linear", "nearest", "previous", "next", "constant"):
                raise ValueError("Unsupported fillmissing method")
            return f"""
if ~exist('{var}','var'), error('Input variable not found.'); end
{out}={var};
{out}(~isfinite({out}))=NaN;
{out}=fillmissing({out},'{method}');
"""
        if operation == "resample_data":
            var = _var(p, "variable", "acquired_data")
            out = _var(p, "output_variable", "resampled_data")
            old = _num(p, "original_sampling_frequency", 1000, 0.000001)
            new = _num(p, "target_sampling_frequency", 500, 0.000001)
            return f"""
if ~exist('{var}','var'), error('Input variable not found.'); end
original_data={var};
if ~isnumeric(original_data)||isempty(original_data)
    error('Input must be a non-empty numeric array.');
end
original_fs={old}; target_fs={new};
original_time=(0:size(original_data,1)-1)'/original_fs;
target_time=(0:1/target_fs:original_time(end))';
{out}=interp1(original_time,original_data,target_time,'linear');
"""
        if operation == "extract_features":
            var = _var(p, "variable", "acquired_data")
            out = _var(p, "output_variable", "signal_features")
            return f"""
if ~exist('{var}','var'), error('Input variable not found.'); end
feature_input={var};
if ~isnumeric(feature_input)||isempty(feature_input)
    error('Input must be a non-empty numeric array.');
end
feature_input=feature_input(:);
feature_input=feature_input(isfinite(feature_input));
if isempty(feature_input), error('No finite samples available.'); end
{out}=struct('sample_count',numel(feature_input), ...
    'mean',mean(feature_input),'rms',sqrt(mean(feature_input.^2)), ...
    'standard_deviation',std(feature_input), ...
    'minimum',min(feature_input),'maximum',max(feature_input), ...
    'peak_to_peak',max(feature_input)-min(feature_input));
disp({out});
"""

    raise ValueError(f"Unknown operation: {skill_name}/{operation}")


# HARMONIC_ANALYSIS_UPGRADE_V2
# Window-corrected FFT, interpolated spectral peaks, frequency estimation,
# and explicit Nyquist-range handling. This is not an IEC-certified analyzer.

_previous_build_code_v2 = build_code

def _enhanced_harmonic_code(params):
    import math

    p = params or {}
    x = p.get("signal", [0, 1, 0, -1, 0, 1, 0, -1])

    if not isinstance(x, (list, tuple)) or len(x) < 16:
        raise ValueError("Harmonic analysis requires at least 16 samples.")
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in x):
        raise ValueError("Signal samples must be finite numeric values.")

    fs = p.get("sampling_frequency", 5000)
    nominal = p.get("fundamental_frequency", 50)
    max_h = p.get("max_harmonic", 10)
    estimate = p.get("estimate_fundamental", True)
    window = p.get("window", "hann")
    fmin = p.get("minimum_frequency", max(1, float(nominal) * 0.8))
    fmax = p.get("maximum_frequency", float(nominal) * 1.2)

    for name, value in (
        ("sampling_frequency", fs),
        ("fundamental_frequency", nominal),
        ("minimum_frequency", fmin),
        ("maximum_frequency", fmax)
    ):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            raise ValueError(name + " must be a positive finite number.")

    if isinstance(max_h, bool) or not isinstance(max_h, int) or not 1 <= max_h <= 100:
        raise ValueError("max_harmonic must be an integer from 1 to 100.")
    if window not in ("hann", "rectangular"):
        raise ValueError("window must be hann or rectangular.")
    if not isinstance(estimate, bool):
        raise ValueError("estimate_fundamental must be true or false.")
    if fmin >= fmax or fmax >= fs / 2:
        raise ValueError("Fundamental search range must be positive and below Nyquist.")
    if float(nominal) >= fs / 2:
        raise ValueError("Nominal fundamental frequency must be below Nyquist.")

    values = " ".join(format(float(v), ".15g") for v in x)
    estimate_matlab = "true" if estimate else "false"
    window_matlab = "'hann'" if window == "hann" else "'rectangular'"

    return f"""
% Enhanced harmonic analysis
x = [{values}];
x = x(:);
fs = {float(fs):.15g};
nominal_fundamental_hz = {float(nominal):.15g};
minimum_frequency_hz = {float(fmin):.15g};
maximum_frequency_hz = {float(fmax):.15g};
max_harmonic_requested = {max_h};
estimate_fundamental = {estimate_matlab};
window_type = {window_matlab};

if numel(x) < 16 || any(~isfinite(x))
    error('Signal must contain at least 16 finite samples.');
end

N = numel(x);
nyquist_hz = fs/2;

if strcmp(window_type,'hann')
    window_vector = 0.5 - 0.5*cos(2*pi*(0:N-1)'/(N-1));
else
    window_vector = ones(N,1);
end

coherent_gain = mean(window_vector);
if coherent_gain <= eps
    error('Invalid window coherent gain.');
end

x_centered = x - mean(x);
windowed_signal = x_centered .* window_vector;
spectrum = fft(windowed_signal);
single_sided_count = floor(N/2)+1;
spectrum_magnitude = abs(spectrum(1:single_sided_count)) / sum(window_vector);
if single_sided_count > 2
    spectrum_magnitude(2:end-1) = 2*spectrum_magnitude(2:end-1);
end
frequency_axis = (0:single_sided_count-1)'*fs/N;

% Estimate the fundamental from the configured search band.
if estimate_fundamental
    search_indices = find(frequency_axis >= minimum_frequency_hz & ...
                          frequency_axis <= maximum_frequency_hz);
    if numel(search_indices) < 3
        error('Insufficient FFT resolution in fundamental search range.');
    end

    [~, local_index] = max(spectrum_magnitude(search_indices));
    peak_index = search_indices(local_index);

    if peak_index <= 1 || peak_index >= single_sided_count
        error('Fundamental peak cannot be interpolated.');
    end

    a = log(max(spectrum_magnitude(peak_index-1),eps));
    b = log(max(spectrum_magnitude(peak_index),eps));
    c = log(max(spectrum_magnitude(peak_index+1),eps));
    denominator = a - 2*b + c;

    if abs(denominator) > eps
        peak_offset = 0.5*(a-c)/denominator;
        peak_offset = max(-0.5,min(0.5,peak_offset));
    else
        peak_offset = 0;
    end

    fundamental_frequency_hz = ...
        (peak_index-1+peak_offset)*fs/N;
else
    fundamental_frequency_hz = nominal_fundamental_hz;
end

if fundamental_frequency_hz <= 0 || fundamental_frequency_hz >= nyquist_hz
    error('Estimated fundamental frequency is outside the measurable range.');
end

% Interpolate amplitude at each harmonic frequency.
measurable_harmonics = min(max_harmonic_requested, ...
    floor((nyquist_hz-eps)/fundamental_frequency_hz));

harmonic_magnitudes = NaN(max_harmonic_requested,1);
harmonic_frequencies_hz = (1:max_harmonic_requested)'*fundamental_frequency_hz;

for h = 1:measurable_harmonics
    target_frequency = h*fundamental_frequency_hz;
    fractional_bin = target_frequency*N/fs;
    nearest_index = round(fractional_bin)+1;

    if nearest_index < 2 || nearest_index >= single_sided_count
        continue;
    end

    left_value = spectrum_magnitude(nearest_index-1);
    center_value = spectrum_magnitude(nearest_index);
    right_value = spectrum_magnitude(nearest_index+1);

    denominator = left_value - 2*center_value + right_value;
    if abs(denominator) > eps
        offset = 0.5*(left_value-right_value)/denominator;
        offset = max(-0.5,min(0.5,offset));
        interpolated_amplitude = center_value - ...
            0.25*(left_value-right_value)*offset;
    else
        interpolated_amplitude = center_value;
    end

    harmonic_magnitudes(h) = max(0,interpolated_amplitude);
end

if measurable_harmonics < 1 || ...
        ~isfinite(harmonic_magnitudes(1)) || harmonic_magnitudes(1) <= eps
    error('Fundamental component is too small for THD calculation.');
end

valid_harmonics = harmonic_magnitudes(2:measurable_harmonics);
valid_harmonics = valid_harmonics(isfinite(valid_harmonics));

thd_percent = 100*sqrt(sum(valid_harmonics.^2))/harmonic_magnitudes(1);

harmonic_results = struct( ...
    'fundamental_frequency_hz',fundamental_frequency_hz, ...
    'fundamental_magnitude',harmonic_magnitudes(1), ...
    'harmonic_frequencies_hz',harmonic_frequencies_hz, ...
    'harmonic_magnitudes',harmonic_magnitudes, ...
    'thd_percent',thd_percent, ...
    'sampling_frequency_hz',fs, ...
    'nyquist_frequency_hz',nyquist_hz, ...
    'requested_harmonics',max_harmonic_requested, ...
    'measurable_harmonics',measurable_harmonics, ...
    'omitted_harmonics',max_harmonic_requested-measurable_harmonics, ...
    'window_type',window_type, ...
    'window_coherent_gain',coherent_gain, ...
    'frequency_estimation_enabled',estimate_fundamental);

disp(harmonic_results);
"""

def build_code(skill_name, operation, params=None):
    if skill_name == "Power Quality and Harmonics" and operation == "harmonic_analysis":
        return _enhanced_harmonic_code(params or {})
    return _previous_build_code_v2(skill_name, operation, params)
