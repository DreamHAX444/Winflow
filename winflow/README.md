# WinFlow

WinFlow is a Windows desktop workflow automation application with a PySide6 GUI,
workflow engine, and command-line runner. For complete prerequisites, setup steps,
and project details, see the [repository README](../README.md).

Run these commands from the repository root after installing dependencies:

```powershell
python -m winflow.ui.app
```

To validate a sample workflow without starting its triggers:

```powershell
python -m winflow.main --config winflow/workflows/example.json --validate-only
```

## Runtime safety settings

- `workflow.enabled` and `settings.enabled` default to `true`; setting either to
  `false` prevents the workflow from running or starting a trigger listener.
- `settings.timeout_seconds` is the default per-action timeout. The older
  `settings.global_timeout_seconds` key remains a supported alias when the
  canonical key is absent; if both are set, `timeout_seconds` wins. A value of
  `0` disables the per-action timeout. Python actions are not force-killed:
  expiry signals cancellation and WinFlow waits for the action to stop before a
  retry, cleanup, or desktop-lock release. A non-cooperative/native blocking
  call can therefore make the observed runtime exceed its configured timeout.
- `settings.emergency_stop_hotkey` accepts a Windows hotkey such as `F8` or
  `CTRL+ALT+F8`. It is registered while the engine runs and requests the same
  emergency stop used by workflow cancellation; shutdown unregisters it.
- Workflow-level `loop.count` must be a finite positive integer. The runner
  does not support `"infinite"`, and validation rejects it.
- Trigger `while_running` policies are `ignore` (drop new events), `queue`
  (run them once in FIFO order), and `restart` (cancel the current run, finish
  cleanup, then run the newest pending event without overlapping executions).
