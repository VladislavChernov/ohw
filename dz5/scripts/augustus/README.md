# Augustus probe scripts (Apache 2.0)

This directory contains Augustus configuration for testing the dz5 security stand
against OWASP LLM Top 10 2026 attack classes.

Augustus (https://github.com/praetorian-inc/augustus) is a Go-based LLM
vulnerability scanner with 210+ probes across 28 providers.

## Usage

```bash
# Install Augustus (Go binary)
go install github.com/praetorian-inc/augustus/cmd/augustus@latest

# List available probes
augustus list

# Run against local Ollama (agent's LLM target)
augustus scan ollama.Ollama \
    --model huihui_ai/llama3.2-abliterate:3b \
    --base-url http://localhost:11434 \
    --probe injection.InstructionOverride \
    --probe jailbreak.DAN \
    --probe encoding.Base64 \
    --probe pii.PIIExfiltration

# Or use the config file
augustus scan --config-file scripts/augustus/augustus.config.yaml

# Run against the Go agent REST endpoint directly
augustus scan rest.REST \
    --endpoint http://localhost:8080/chat \
    --probe injection.InstructionOverride

# Run against preset 5 (agent-fixed)
augustus scan rest.REST \
    --endpoint http://localhost:8081/chat \
    --probe injection.InstructionOverride \
    --probe jailbreak.DAN
```

## Covered OWASP LLM Top 10 2026 classes

- LLM01: Prompt Injection
- LLM02: Insecure Output Handling
- LLM03: Training Data Poisoning (via tool abuse)
- LLM04: Model Denial of Service
- LLM05: Supply Chain Attacks
- LLM06: Sensitive Information Disclosure (PII)
- LLM07: Insecure Plugin Design
- LLM08: Excessive Agency
- LLM09: Overreliance
- LLM10: Model Theft

Results are written to `reports/augustus/`.

## License

Apache 2.0 (Praetorian Security, Inc.)