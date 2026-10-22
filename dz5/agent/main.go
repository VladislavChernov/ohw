package main

import (
	"database/sql"
	"log"
	"net/http"
	"os"

	"ohw/dz5/agent/internal/agentcore"

	_ "modernc.org/sqlite"
)

func main() {
	logger := log.New(os.Stdout, "[agent] ", log.LstdFlags)
	promptsDir := os.Getenv("PROMPTS_DIR")
	if promptsDir == "" {
		promptsDir = "prompts"
	}
	minimal, guarded, err := agentcore.LoadPrompts(promptsDir)
	if err != nil {
		logger.Fatalf("load system prompts: %v", err)
	}
	cfg := agentcore.LoadConfig()
	cfg.MinimalPrompt = minimal
	cfg.GuardedPrompt = guarded
	cfg.AgentName = "baseline"
	settings := agentcore.SettingsFromEnv()
	logger.Printf("agent=baseline preset=%d builtin=%v external=%v allow_writes=%v runtime_toggles=%v llm=%s model=%s wall=%s",
		agentcore.PresetOf(settings), settings.BuiltinGuards, settings.ExternalGuard, settings.AllowWrites,
		cfg.RuntimeToggles(), cfg.LLMBaseURL, cfg.LLMModel, cfg.WallBaseURL)

	db, err := sql.Open("sqlite", cfg.DBPath)
	if err != nil {
		logger.Fatalf("open db %s: %v", cfg.DBPath, err)
	}
	defer db.Close()
	if err := db.Ping(); err != nil {
		logger.Fatalf("ping db %s: %v", cfg.DBPath, err)
	}

	audit, err := agentcore.NewAuditLogger(cfg.AuditLog)
	if err != nil {
		logger.Fatalf("audit log: %v", err)
	}

	engine := agentcore.NewEngine(cfg, agentcore.Mode{
		AgentName:       "baseline",
		InitialSettings: settings,
	}, db, audit, logger)

	logger.Printf("listening on %s", cfg.Addr)
	if err := http.ListenAndServe(cfg.Addr, engine.Mux()); err != nil {
		logger.Fatalf("serve: %v", err)
	}
}
