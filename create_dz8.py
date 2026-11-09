import os

base = r"D:\Otus\ohw\dz8"

# Create directories
dirs = [
    "src/llm_degradation",
    "ui",
    "input",
    "golden",
    "output",
    "tests",
    "output/runs",
]

for d in dirs:
    os.makedirs(os.path.join(base, d), exist_ok=True)

# Create __init__.py
init_path = os.path.join(base, "src/llm_degradation/__init__.py")
with open(init_path, "w", encoding="utf-8") as f:
    f.write('"""llm-degradation-lab: Checking local LLM for hallucinations, inconsistency, and degradation."""\n\n__version__ = "0.1.0"\n')

# Create pyproject.toml (already created, skip)

# Create config.toml (already created, skip)

print("Directory structure created successfully")