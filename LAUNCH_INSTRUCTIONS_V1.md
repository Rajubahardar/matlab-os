# Local AI OS V1 Release Candidate - Launch Instructions

## Version Declaration
Local AI OS V1 is declared as a **RELEASE CANDIDATE**. Feature development is frozen. 
Phase 5, V2, additional audits, and architectural improvements are not started.

## MATLAB Integration (Phase 4 Verified)
- **MATLAB Executable**: `D:\Apps\Matlab\bin\matlab.exe`
- **MATLAB Version**: R2024b (24.2.0.2806996)
- **Integration Status**: Connected and verified working
- **Test Command**: `disp('Local AI OS MATLAB connection successful')` executes successfully

## Known Ollama Test Failures (Preserved)
The following two Ollama-related tests are known to fail in the V1 release, 
and are explicitly documented in the release notes without claiming a completely green test suite:

1. **test_ollama_provider_constructs_with_reused_adapter** - Fails when the Ollama 
   provider is constructed without a live Ollama service running. The `is_configured()` 
   method returns `False` because no Ollama adapter is available at the default endpoint.

2. **test_registry_from_default_config_never_builds_disabled_deepseek** - Fails when 
   the ProviderRegistry configuration has DeepSeek disabled and Ollama enabled, but the 
   registry fails to properly identify Ollama as the enabled provider. This is a config-
   resolution issue that will be fixed post-V1.

These failures are expected in the V1 release and will be addressed in post-V1 optimization.

## Release Freeze Status
- ✅ Feature development frozen
- ✅ Phase 4 verification complete
- ✅ MATLAB integration configured and tested
- ⚠️ Two known Ollama test failures documented
- ⚠️ Release candidate declared - V1 launch instructions active

## V1 Usage Period (Next 6 Months)
The next six months are for real daily usage, observing practical failures, 
and fixing only issues that genuinely affect V1 functionality. No new features 
will be added during this period.

## Configuration
- **config.toml**: Updated with `engine_path = "D:\Apps\Matlab\bin\matlab.exe"` and version `1.0.0-rc`
- **.env.example**: Updated with `MATLAB_ENGINE_PATH=D:\Apps\Matlab\bin\matlab.exe`

## Verification Results
- MATLAB executable discovered automatically at `D:\Apps\Matlab\bin\matlab.exe`
- MATLAB version detected: R2024b
- MATLAB command execution test: **PASSED** - `disp('Local AI OS MATLAB connection successful')`
