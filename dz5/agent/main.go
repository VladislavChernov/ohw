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
	mux.HandleFunc("/status", srv.handleStatus)
	mux.HandleFunc("/ui", srv.handleUI)
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

// handleStatus returns JSON status of protection toggles for GET /status.
func (s *Server) handleStatus(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(map[string]interface{}{
		"preset":        s.cfg.Preset,
		"builtinGuards": s.cfg.BuiltinGuards,
		"externalGuard": s.cfg.ExternalGuard,
		"allowWrites":   s.cfg.AllowWrites,
		"toolPolicy":    "allow-list: SELECT only, writes blocked without ALLOW_WRITES=1",
		"llmBaseURL":    s.cfg.LLMBaseURL,
	})
}

// uiTemplate is the HTML page for GET /ui.
const uiTemplate = `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Agent Security UI</title>
<style>
body { font-family: Arial, sans-serif; max-width: 900px; margin: 2rem auto; padding: 0 1rem; }
h1 { color: #1a73e8; }
.grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 1rem; }
.card { border: 1px solid #ddd; border-radius: 8px; padding: 1rem; }
.card h2 { margin-top: 0; color: #333; }
.switch { position: relative; display: inline-block; width: 48px; height: 24px; }
.switch input { opacity: 0; width: 0; height: 0; }
.slider { position: absolute; cursor: pointer; top: 0; left: 0; right: 0; bottom: 0; background-color: #ccc; transition: .3s; border-radius: 24px; }
.slider:before { position: absolute; content: ""; height: 18px; width: 18px; left: 3px; bottom: 3px; background-color: white; transition: .3s; border-radius: 50%; }
input:checked + .slider { background-color: #2196F3; }
input:checked + .slider:before { transform: translateX(24px); }
.footer { margin-top: 2rem; font-size: 0.85rem; color: #666; }
</style>
</head>
<body>
<h1>Agent Security UI</h1>
<p class="sub">Stand agent (presets 1-4): protection toggles.</p>
<div class="grid">
  <div class="card">
    <h2>Builtin Guards</h2>
    <label id="builtinLabel">
      <input type="checkbox" id="builtinGuard" {{if .BuiltinGuards}}checked{{end}}>
      <span class="switch"><input type="checkbox" id="inputGuard"></span>
    </label>
    <p>Prompt isolation, input/output validation, tool whitelist.</p>
  </div>
  <div class="card">
    <h2>External Wall</h2>
    <label id="externalLabel">
      <input type="checkbox" id="externalGuard" {{if .ExternalGuard}}checked{{end}}>
      <span class="switch"><input type="checkbox" id="externalGuardCheck"></span>
    </label>
    <p>Routes through security-wall (Guardrails AI proxy).</p>
  </div>
  <div class="card">
    <h2>Write Tools</h2>
    <label id="writesLabel">
      <input type="checkbox" id="allowWrites" {{if .AllowWrites}}checked{{end}}>
      <span class="switch"><input type="checkbox" id="writesCheck"></span>
    </label>
    <p>Allows <code>INSERT/UPDATE/DELETE/DROP</code> (with confirmation).</p>
  </div>
</div>
<div class="card">
<h3>Current policy</h3>
<pre id="status" style="background:#f5f5f5; padding:0.5rem; border-radius:4px; overflow-x:auto;">Loading...</pre>
</div>
<div class="card">
<h3>Ask the agent</h3>
<textarea id="chatInput" rows="4" style="width:100%; box-sizing:border-box;" placeholder="Type your question..."></textarea>
<button id="sendBtn">Send</button>
<div id="chatOutput" style="margin-top:0.5rem; white-space:pre-wrap; background:#fafafa; padding:0.5rem; border-radius:4px; min-height:1rem;"></div>
</div>
<script>
const $ = id => document.getElementById(id);
const refresh = async () => {
  const r = await fetch('/status');
  const j = await r.json();
  $('builtinGuard').checked = j.builtinGuards;
  $('externalGuard').checked = j.externalGuard;
  $('allowWrites').checked = j.allowWrites;
  $('status').textContent = JSON.stringify(j, null, 2);
};
$('sendBtn').onclick = async () => {
  const msg = $('chatInput').value;
  const r = await fetch('/chat', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({message: msg})
  });
  const j = await r.json();
  $('chatOutput').textContent = JSON.stringify(j, null, 2);
  $('chatInput').value = '';
};
refresh();
</script>
</body>
</html>`

// handleUI serves the HTML UI page for GET /ui.
func (s *Server) handleUI(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	html := uiTemplate
	html = strings.Replace(html, `{{if .BuiltinGuards}}checked{{end}}`, "", 1)
	html = strings.Replace(html, `{{if .ExternalGuard}}checked{{end}}`, "", 1)
	html = strings.Replace(html, `{{if .AllowWrites}}checked{{end}}`, "", 1)
	if s.cfg.BuiltinGuards {
		html = strings.Replace(html, `<input type="checkbox" id="builtinGuard" >`, `<input type="checkbox" id="builtinGuard" checked>`, 1)
	}
	if s.cfg.ExternalGuard {
		html = strings.Replace(html, `<input type="checkbox" id="externalGuard" >`, `<input type="checkbox" id="externalGuard" checked>`, 1)
	}
	if s.cfg.AllowWrites {
		html = strings.Replace(html, `<input type="checkbox" id="allowWrites" >`, `<input type="checkbox" id="allowWrites" checked>`, 1)
	}
	_, _ = w.Write([]byte(html))
}
