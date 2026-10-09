# WinFlow - Desktop Workflow Automation

A configurable Windows desktop automation and notification-triggered workflow framework with PySide6 GUI.

---

## 🚀 Requirements & Prerequisites

- **OS:** Windows 10 or Windows 11 (64-bit recommended)
- **Python:** Python 3.10, 3.11, 3.12+ (tested up to Python 3.14)

---

## 📦 Setup & Installation

1. **Clone / Open the Project Directory:**
   ```powershell
   git clone https://github.com/DreamHAX444/Winflow.git
   cd Winflow
   ```

2. **Create a Virtual Environment (Recommended):**
   ```powershell
   python -m venv .venv
   ```

3. **Activate the Virtual Environment:**
   - **PowerShell:**
     ```powershell
     .\.venv\Scripts\Activate.ps1
     ```
   - **Command Prompt (CMD):**
     ```cmd
     .venv\Scripts\activate.bat
     ```

4. **Install Dependencies:**
   ```powershell
   pip install -r requirements.txt
   ```

---

## 🖥️ Running the Application

### 1. Launch GUI (Visual Workflow Editor & Dashboard)
```powershell
python -m winflow.ui.app
```

### 2. Run Engine via CLI
To run or listen for triggers on a specific workflow file:
```powershell
python -m winflow.main --config winflow/workflows/example.json
```
or
```powershell
python -m winflow.main --config winflow/workflows/wa.json
```

Validate a workflow configuration file without starting listeners:
```powershell
python -m winflow.main --config winflow/workflows/example.json --validate-only
```

---

## 🧪 Running Tests
```powershell
python -m unittest discover tests
```

---

## 📁 Project Structure
- `winflow/` - Core application package
  - `actions/` - Mouse, keyboard, window, clipboard, app launch, wait, variables
  - `backend/` - Low-level Windows API integration (Win32, ctypes)
  - `config/` - Schema definitions, JSON/YAML loaders, validation
  - `core/` - Lock, emergency stop, error types, logging, diagnostics
  - `engine/` - Workflow runner, registry, execution context
  - `triggers/` - Windows notification listener, scheduler, dispatcher
  - `ui/` - PySide6 modern UI (Editor, Trigger Editor, Pickers, Themes)
  - `verification/` - Step verifications (window active, process running)
  - `workflows/` - Sample workflows
- `tests/` - Comprehensive test suite
- `requirements.txt` - Python package dependencies
