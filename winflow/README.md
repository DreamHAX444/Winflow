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
