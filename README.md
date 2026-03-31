# Python Executor MCP Server

[![MCP Server](https://img.shields.io/badge/MCP-Server-blue)](https://modelcontextprotocol.io)
[![Python](https://img.shields.io/badge/Python-3.10%2B-green)](https://www.python.org)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![CI](https://img.shields.io/github/actions/workflow/status/neco001/py_executor/ci.yml)](https://github.com/neco001/py_executor/actions/workflows/ci.yml)

Execute Python code snippets with shell-quoting-free execution via temporary files.

**Features:**

- **Batch execution** - Run multiple snippets in parallel (max 4 workers)
- **Security-first** - Size limits, type validation, UUID temp files
- **Helper utilities** - `chunk_by_count()`, `suggest_batch_size()` for task decomposition
- **Zero dependencies** - Only `mcp` required, uses stdlib for execution

## Why Do I Need This?

Ever been frustrated when your AI agent tries to execute a simple one-liner and gets lost in shell quoting hell?

```
# Agent tries to run this:
print("Hello 'world' with \"quotes\" and $variables")

# Shell interprets quotes, variables, escapes...
# Result: SyntaxError, FileNotFoundError, or worse - unexpected behavior
```

**This server solves the problem by:**

1. Writing code directly to a temp file (no shell interpretation)
2. Executing the file with Python (clean, predictable)
3. Returning stdout/stderr/exit_code (full visibility)

**Bonus:** Batch execution lets you run 20 independent tasks in parallel instead of sequentially.

## Installation

### Quick Start

```bash
# Clone the repository
git clone https://github.com/neco001/py-executor.git
cd py-executor

# Install dependencies with uv
uv sync

# Run the server
uv run python server.py
```

### Configure with Claude Desktop / Roo / Other MCP Clients

Add to your MCP client configuration:

```json
{
  "mcpServers": {
    "py-executor": {
      "command": "uv",
      "args": [
        "--directory",
        "/path/to/py-executor",
        "run",
        "python",
        "server.py"
      ]
    }
  }
}
```

### Alternative: Direct Python

```json
{
  "mcpServers": {
    "py-executor": {
      "command": "python",
      "args": ["/path/to/py-executor/server.py"]
    }
  }
}
```

## Tools

### `run_python` - Single Execution

Execute a single Python code snippet. Use for stateful/dependent operations.

**Parameters:**

- `code`: Python code to execute (max 1MB)
- `timeout`: Execution timeout in seconds (default 30, max 60)
- `cwd`: Optional working directory - uses local .venv if found

**When to use:**

- Running a single calculation or analysis
- When code needs to maintain state between operations
- When subsequent code depends on previous results
- For interactive debugging or testing small code snippets

**Examples:**

```python
# Simple calculation
code = "result = 2 + 2\nprint(f'Result: {result}')"

# Data analysis on single dataset
code = "import pandas as pd\ndf = pd.DataFrame({'a': [1,2,3]})\nprint(df.sum())"

# File processing (single file)
code = "with open('data.txt', 'r') as f:\n    content = f.read()\n    print(len(content))"
```

---

### `run_python_batch` - Parallel Execution

Execute multiple Python code snippets in parallel (max 4 workers, max 20 snippets).

**Parameters:**

- `codes`: List of Python code strings to execute (max 20, each max 1MB)
- `timeout`: Timeout per snippet in seconds (default 30, max 60)
- `max_workers`: Maximum parallel workers (default 4, hard capped at 4)
- `cwd`: Optional working directory - uses local .venv if found

**When to use:**

- Processing multiple files independently
- Running parallel data transformations on separate datasets
- When tasks are completely independent and don't share state
- For batch processing of similar operations across different inputs

**When NOT to use:**

- When code snippets depend on each other's results
- When maintaining shared state between executions
- For sequential operations where order matters

**Examples:**

```python
# Processing multiple files independently
codes = [
    "with open('file1.txt', 'r') as f: print(len(f.read()))",
    "with open('file2.txt', 'r') as f: print(len(f.read()))",
    "with open('file3.txt', 'r') as f: print(len(f.read()))"
]
run_python_batch(codes)

# Parallel data analysis on separate datasets
codes = [
    "import pandas as pd; df = pd.read_csv('data1.csv'); print(df.shape)",
    "import pandas as pd; df = pd.read_csv('data2.csv'); print(df.shape)",
    "import pandas as pd; df = pd.read_csv('data3.csv'); print(df.shape)"
]
run_python_batch(codes)

# Independent calculations
codes = [
    "result = sum(range(1000)); print(result)",
    "import math; result = math.factorial(10); print(result)",
    "import random; result = [random.randint(1, 100) for _ in range(5)]; print(result)"
]
run_python_batch(codes)
```

---

## Helper Utilities

Internal functions to help agents split large tasks into batch-friendly chunks.

### `chunk_by_count(items: list, n: int) -> list[list]`

Split a list into n approximately equal-sized chunks.

```python
files = ['file1.py', 'file2.py', 'file3.py', 'file4.py']
chunks = chunk_by_count(files, 2)
# Result: [['file1.py', 'file2.py'], ['file3.py', 'file4.py']]

# Use with run_python_batch:
chunks = chunk_by_count(files, 4)
codes = [f"process_files({chunk})" for chunk in chunks]
run_python_batch(codes)
```

### `chunk_by_size(code: str, max_bytes: int = 500000) -> list[str]`

Split a large code string into smaller chunks based on byte size.

```python
large_code = "process_data('file1')\nprocess_data('file2')\n..."
chunks = chunk_by_size(large_code, 500000)  # 500KB per chunk
run_python_batch(chunks)
```

### `suggest_batch_size(total_items: int, complexity: str = "medium") -> int`

Suggest an optimal batch size based on total items and task complexity.

```python
files = list_of_100_files
workers = suggest_batch_size(len(files), "low")  # Returns 4 for simple tasks
workers = suggest_batch_size(len(files), "high")  # Returns 2 for heavy tasks

chunks = chunk_by_count(files, workers)
codes = [f"analyze_files({chunk})" for chunk in chunks]
run_python_batch(codes, max_workers=workers)
```

**Complexity levels:**

- `"low"`: Simple operations (e.g., file size checks) → max 4 workers
- `"medium"`: Moderate operations (e.g., data parsing) → 3 workers
- `"high"`: Heavy operations (e.g., ML inference) → 2 workers

---

## Performance & Limits

| Parameter      | Limit                  |
| -------------- | ---------------------- |
| Max workers    | 4 (hard cap)           |
| Max batch size | 20 snippets            |
| Max code size  | 1MB per snippet        |
| Max timeout    | 60 seconds per snippet |

---

## Architecture

```
run_python_batch(codes: list[str])
    └── ProcessPoolExecutor(max_workers=4)
            ├── Worker 1: _execute_single_snippet(0, code, timeout, cwd)
            ├── Worker 2: _execute_single_snippet(1, code, timeout, cwd)
            ├── Worker 3: _execute_single_snippet(2, code, timeout, cwd)
            └── Worker 4: _execute_single_snippet(3, code, timeout, cwd)
                    └── UUID temp file → subprocess.run → cleanup
            └── Results collected by index → ordered JSON response
```

---

## Security

- UUID-named temp files prevent collisions
- Input validation: type checking, size limits
- Timeout enforcement prevents zombie processes
- ProcessPoolExecutor provides crash containment
- No shell execution (shell=False) prevents injection
