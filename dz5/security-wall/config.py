"""Guardrails Server config for the dz5 security wall.

The server imports this file through GR_CONFIG_FILE_PATH and registers every
Guard instance found in the module globals. The Guard id is the URL path
segment used by the agent:

    POST /guards/input-defense/validate
    POST /guards/output-privacy/validate
"""

import guardrails_ai.detect_pii.main as _detect_pii
from guardrails import Guard, OnFailAction
from guardrails_ai.detect_pii import DetectPII
from guardrails_ai.detect_prompt_injection import DetectPromptInjection

_shared_analyzer = _detect_pii.AnalyzerEngine()
_shared_anonymizer = _detect_pii.AnonymizerEngine()
_detect_pii.AnalyzerEngine = lambda: _shared_analyzer
_detect_pii.AnonymizerEngine = lambda: _shared_anonymizer

# Input guard: prompt injection / jailbreak. NOOP keeps the HTTP contract at
# 200 with validationPassed=false instead of raising, so the agent can apply
# its own fail-closed policy uniformly.
input_guard = Guard(id="input-defense", name="input-defense").use(
    DetectPromptInjection(
        check_vector=False,
        on_fail=OnFailAction.NOOP,
    )
)

# Output guard: PII is fixed (masked) by the validator. The agent compares
# validatedOutput with rawLlmOutput to detect that a redaction happened.
# PERSON is intentionally excluded: Presidio/spaCy NER on user/name-heavy text
# spins at >500% CPU for minutes in this stand. Regex entities stay cheap.
output_guard = Guard(id="output-privacy", name="output-privacy").use(
    DetectPII(
        pii_entities=[
            "EMAIL_ADDRESS",
            "PHONE_NUMBER",
            "CREDIT_CARD",
        ],
        on_fail=OnFailAction.FIX,
    )
)
