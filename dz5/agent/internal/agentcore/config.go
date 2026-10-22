package agentcore

import (
	"os"
	"strings"
	"time"
)

type Config struct {
	Addr          string
	MinimalPrompt string
	GuardedPrompt string
	FixedPrompt   string
	LLMBaseURL    string
	LLMModel      string
	DBPath        string

	WallBaseURL     string
	InputGuardName  string
	OutputGuardName string
	WallTimeout     time.Duration
	BlockOnFail     bool

	AllowRuntimeToggles bool
	AuditLog            string

	AgentName string
	Fixed     bool
}

func (c Config) PromptFor(s RuntimeSettings) string {
	if c.Fixed {
		return c.FixedPrompt
	}
	if s.BuiltinGuards {
		return c.GuardedPrompt
	}
	return c.MinimalPrompt
}

func (c Config) RuntimeToggles() bool {
	return c.AllowRuntimeToggles && !c.Fixed
}

func (c Config) EffectiveGuarded(s RuntimeSettings) bool {
	return c.Fixed || s.BuiltinGuards
}

func getenv(key, def string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return def
}

func durationEnv(key string, def time.Duration) time.Duration {
	raw := getenv(key, "")
	if raw == "" {
		return def
	}
	d, err := time.ParseDuration(raw)
	if err != nil || d <= 0 {
		return def
	}
	return d
}

func LoadConfig() Config {
	return Config{
		Addr:                getenv("AGENT_ADDR", ":8080"),
		LLMBaseURL:          strings.TrimRight(getenv("OLLAMA_BASE_URL", "http://ollama:11434"), "/"),
		LLMModel:            getenv("OLLAMA_MODEL", "huihui_ai/llama3.2-abliterate:3b"),
		DBPath:              getenv("DB_PATH", "/data/db.sqlite"),
		WallBaseURL:         strings.TrimRight(getenv("GUARDRAILS_BASE_URL", "http://wall:8000"), "/"),
		InputGuardName:      getenv("INPUT_GUARD_NAME", "input-defense"),
		OutputGuardName:     getenv("OUTPUT_GUARD_NAME", "output-privacy"),
		WallTimeout:         durationEnv("GUARDRAILS_TIMEOUT", 60*time.Second),
		BlockOnFail:         !strings.EqualFold(getenv("BLOCK_ON_FAIL", "on"), "off"),
		AllowRuntimeToggles: strings.EqualFold(getenv("ALLOW_RUNTIME_TOGGLES", "off"), "on"),
		AuditLog:            getenv("AUDIT_LOG", "/data/audit.jsonl"),
		AgentName:           "baseline",
	}
}

func SettingsFromEnv() RuntimeSettings {
	truthy := func(v string) bool {
		return strings.EqualFold(v, "on") || v == "1" || strings.EqualFold(v, "true")
	}
	return RuntimeSettings{
		BuiltinGuards: truthy(getenv("BUILTIN_GUARDS", "off")),
		ExternalGuard: truthy(getenv("EXTERNAL_GUARD", "off")),
		AllowWrites:   truthy(getenv("ALLOW_WRITES", "off")),
	}
}
