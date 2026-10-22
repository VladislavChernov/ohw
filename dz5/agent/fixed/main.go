package main

import (
	"database/sql"
	"log"
	"net/http"
	"os"
	"path/filepath"

	"ohw/dz5/agent/internal/agentcore"

	_ "modernc.org/sqlite"
)

func promptPath() string {
	dir := os.Getenv("PROMPTS_DIR")
	if dir == "" {
		dir = "prompts"
		if _, err := os.Stat(filepath.Join(dir, "fixed.txt")); err != nil {
			dir = filepath.Join("fixed", "prompts")
		}
	}
	return filepath.Join(dir, "fixed.txt")
}

func main() {
	logger := log.New(os.Stdout, "[agent-fixed] ", log.LstdFlags)
	fixedPrompt, err := agentcore.ReadPrompt(promptPath())
	if err != nil {
		logger.Fatalf("load fixed prompt: %v", err)
	}
	cfg := agentcore.LoadConfig()
	cfg.AgentName = "fixed"
	cfg.Fixed = true
	cfg.FixedPrompt = fixedPrompt
	cfg.AllowRuntimeToggles = false

	settings := agentcore.RuntimeSettings{BuiltinGuards: true}
	logger.Printf("agent=fixed preset=5 builtin=true external=false allow_writes=false llm=%s model=%s db=%s",
		cfg.LLMBaseURL, cfg.LLMModel, cfg.DBPath)

	db, err := sql.Open("sqlite", cfg.DBPath)
	if err != nil {
		logger.Fatalf("open db %s: %v", cfg.DBPath, err)
	}
	defer db.Close()
	if err := db.Ping(); err != nil {
		logger.Fatalf("ping db %s: %v", cfg.DBPath, err)
	}

	guard := NewFixedGuard()
	engine := agentcore.NewEngine(cfg, agentcore.Mode{
		AgentName:       "fixed",
		Fixed:           true,
		InitialSettings: settings,
		ValidateInput:   guard.CheckInput,
		ValidateOutput:  guard.CheckOutput,
		ValidateSQL:     ValidateFixedSQL,
	}, db, nil, logger)

	logger.Printf("listening on %s", cfg.Addr)
	if err := http.ListenAndServe(cfg.Addr, engine.Mux()); err != nil {
		logger.Fatalf("serve: %v", err)
	}
}
