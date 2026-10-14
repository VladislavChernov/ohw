// Package main implements the dz5 stand agent (Go).
//
// Milestone add-agent-core, preset 1 (Baseline / No Safety): a minimal HTTP
// agent that talks to the local abliterated LLM target and reads the SQLite
// stand DB through the db_query tool. Builtin guards and the external wall
// arrive in later milestones via env switches (BUILTIN_GUARDS, EXTERNAL_GUARD).
package main

import (
	"database/sql"
	"encoding/json"
	"log"
	"net/http"
	"os"
	"strings"

	_ "modernc.org/sqlite"
)

// Config holds the runtime switches of the stand agent.
// Preset matrix (see openspec/changes/add-agent-core/specs/agent-core/spec.md):
// BUILTIN_GUARDS off/on x EXTERNAL_GUARD off/on -> presets 1..4.
type Config struct {
	Addr          string // :8080
	SystemPrompt  string // minimal.txt (preset 1) or guarded.txt (presets 2/4)
	LLMBaseURL    string // OLLAMA_BASE_URL or WALL_BASE_URL when EXTERNAL_GUARD=on
	LLMModel      string // OLLAMA_MODEL
	DBPath        string // /data/db.sqlite
	AllowWrites   bool   // ALLOW_WRITES=1 opens tool-abuse demo (LLM03)
	BuiltinGuards bool
	ExternalGuard bool
	Preset        int // 1..4 from the BUILTIN_GUARDS x EXTERNAL_GUARD matrix
}

// ChatRequest is the inbound HTTP contract: POST /chat {"message": "..."}.
type ChatRequest struct {
	Message string `json:"message"`
}

// ToolCall records one db_query invocation for the response envelope.
type ToolCall struct {
	SQL  string `json:"sql"`
	Rows int    `json:"rows"`
}

// ChatResponse is the outbound contract.
type ChatResponse struct {
	Reply     string     `json:"reply"`
	Preset    int        `json:"preset"`
	ToolCalls []ToolCall `json:"tool_calls"`
}

// Server wires HTTP handlers to the LLM client and the DB handle.
type Server struct {
	cfg Config
	db  *sql.DB
	llm *OllamaClient
	log *log.Logger
}

func getenv(key, def string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return def
}

func loadConfig(prompt string) Config {
	builtin := strings.EqualFold(getenv("BUILTIN_GUARDS", "off"), "on")
	external := strings.EqualFold(getenv("EXTERNAL_GUARD", "off"), "on")
	llmURL := getenv("OLLAMA_BASE_URL", "http://ollama:11434")
	if external {
		llmURL = getenv("WALL_BASE_URL", "http://wall:5000")
	}
	preset := 1
	if builtin && external {
		preset = 4
	} else if builtin {
		preset = 2
	} else if external {
		preset = 3
	}
	return Config{
		Addr:          getenv("AGENT_ADDR", ":8080"),
		SystemPrompt:  prompt,
		LLMBaseURL:    strings.TrimRight(llmURL, "/"),
		LLMModel:      getenv("OLLAMA_MODEL", "huihui_ai/llama3.2-abliterate:3b"),
		DBPath:        getenv("DB_PATH", "/data/db.sqlite"),
		AllowWrites:   strings.EqualFold(getenv("ALLOW_WRITES", "off"), "on") || getenv("ALLOW_WRITES", "") == "1",
		BuiltinGuards: builtin,
		ExternalGuard: external,
		Preset:        preset,
	}
}

func main() {
	logger := log.New(os.Stdout, "[agent] ", log.LstdFlags)
	prompt, err := loadPrompt()
	if err != nil {
		logger.Fatalf("load system prompt: %v", err)
	}
	cfg := loadConfig(prompt)
	logger.Printf("preset=%d builtin=%v external=%v allow_writes=%v llm=%s model=%s",
		presetOf(cfg), cfg.BuiltinGuards, cfg.ExternalGuard, cfg.AllowWrites, cfg.LLMBaseURL, cfg.LLMModel)

	db, err := sql.Open("sqlite", cfg.DBPath)
	if err != nil {
		logger.Fatalf("open db %s: %v", cfg.DBPath, err)
	}
	defer db.Close()
	if err := db.Ping(); err != nil {
		logger.Fatalf("ping db %s: %v", cfg.DBPath, err)
	}

	srv := &Server{cfg: cfg, db: db, llm: NewOllamaClient(cfg.LLMBaseURL, cfg.LLMModel), log: logger}
	mux := http.NewServeMux()
	mux.HandleFunc("/healthz", func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte("ok"))
	})
	mux.HandleFunc("/chat", srv.handleChat)
	logger.Printf("listening on %s", cfg.Addr)
	if err := http.ListenAndServe(cfg.Addr, mux); err != nil {
		logger.Fatalf("serve: %v", err)
	}
}

// presetOf recovers the preset number from the config flags.
func presetOf(cfg Config) int {
	return cfg.Preset
}

// handleChat runs the tool-loop: LLM -> db_query -> LLM -> reply.
func (s *Server) handleChat(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	var req ChatRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil || strings.TrimSpace(req.Message) == "" {
		http.Error(w, "bad request: {\"message\": \"...\"} required", http.StatusBadRequest)
		return
	}
	reply, calls, err := s.runToolLoop(r.Context(), req.Message)
	if err != nil {
		s.log.Printf("chat error: %v", err)
		http.Error(w, "llm/db error: "+err.Error(), http.StatusBadGateway)
		return
	}
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(ChatResponse{Reply: reply, Preset: presetOf(s.cfg), ToolCalls: calls})
}
