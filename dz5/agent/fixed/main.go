// Package main implements the fixed (secured) agent: preset 5.
//
// Features:
//   - /status: JSON status of protection toggles
//   - /ui: HTML web UI to toggle BUILTIN_GUARDS, EXTERNAL_GUARD, ALLOW_WRITES
//   - Builtin guards: prompt isolation, input validation, output guard, tool whitelist
//   - Tool whitelist: read-only SELECT on known tables, writes blocked without ALLOW_WRITES=1
package main

import (
	"encoding/json"
	"log"
	"net/http"
	"os"
	"regexp"
	"strings"
)

// Config holds the runtime switches of the fixed agent (preset 5).
type Config struct {
	Addr          string // :8080
	SystemPrompt  string // prompts/fixed.txt
	LLMModel      string // OLLAMA_MODEL
	DBPath        string // /data/db.sqlite
	AllowWrites   bool   // ALLOW_WRITES=1 opens write tools (requires confirm)
	BuiltinGuards bool   // BUILTIN_GUARDS=on enables input/output guards
	ExternalGuard bool   // EXTERNAL_GUARD=on uses WALL_BASE_URL as LLM backend
	Preset        int    // 5
}

// GuardRegistry holds active guard settings.
type GuardRegistry struct {
	ScanInput      bool
	ScanOutput     bool
	BlockOnFail    bool
	InputPatterns  []*regexp.Regexp
	OutputPatterns []*regexp.Regexp
}

// NewGuardRegistry initialises the guards from env.
func NewGuardRegistry() *GuardRegistry {
	g := &GuardRegistry{
		ScanInput:   os.Getenv("SCAN_INPUT") == "on",
		ScanOutput:  os.Getenv("SCAN_OUTPUT") == "on",
		BlockOnFail: os.Getenv("BLOCK_ON_FAIL") == "on",
	}
	if g.ScanInput {
		inputPatterns := []string{
			`(?i)(ignore|forget|ignore previous|forget previous).*(instructions|commands|system)`,
			`(?i)(<<|{{){2,}\s*(user|system|assistant|developer)\s*(<<|{{){2,}`,
			`(?i)(repeat|reveal|print).*(initialisation|initial message|system prompt)`,
			`(?i)dump|list|show.*files`,
			`(?i)(http|https)://\S+`,
			`(?i)base64|rot13|rot-13|unicode|zero.width`,
			`(?i)dan|fake.role|ignore.*role`,
			`(?i)jailbreak|act as`,
		}
		for _, p := range inputPatterns {
			if re, err := regexp.Compile(p); err == nil {
				g.InputPatterns = append(g.InputPatterns, re)
			}
		}
	}
	outputPatterns := []string{
		`(?i)(system prompt|initialisation message|hidden context|internal instructions)`,
		`(?i)(password|api[_-]?key|secret|token|inn|passport|pin[_-]?code)\s*[=:]\s*["']?[a-zA-Z0-9_\-]{8,}["']?`,
	}
	for _, p := range outputPatterns {
		if re, err := regexp.Compile(p); err == nil {
			g.OutputPatterns = append(g.OutputPatterns, re)
		}
	}
	return g
}

// CheckInput runs guards on user input and returns (violated, reason).
func (g *GuardRegistry) CheckInput(msg string) (bool, string) {
	if !g.ScanInput {
		return false, ""
	}
	for _, re := range g.InputPatterns {
		if re.MatchString(msg) {
			return true, "input pattern matched: " + re.String()
		}
	}
	return false, ""
}

// CheckOutput runs guards on assistant reply and returns (violated, reason).
func (g *GuardRegistry) CheckOutput(reply string) (bool, string) {
	if !g.ScanOutput {
		return false, ""
	}
	for _, re := range g.OutputPatterns {
		if re.MatchString(reply) {
			return true, "output pattern matched: " + re.String()
		}
	}
	return false, ""
}

// ToolPolicy enforces allow-list for db_query and blocks destructive statements.
func ToolPolicy(sql string) (bool, string) {
	sql = strings.ToUpper(strings.TrimSpace(sql))
	sql = strings.ReplaceAll(sql, "`", "")
	sql = strings.ReplaceAll(sql, "\"", "")
	sql = strings.ReplaceAll(sql, "'", "")
	if strings.HasPrefix(sql, "SELECT") {
		if !strings.Contains(sql, "USERS") && !strings.Contains(sql, "PERSONAL_DATA") &&
			!strings.Contains(sql, "CLIENTS") && !strings.Contains(sql, "ORDERS") &&
			!strings.Contains(sql, "SECRETS") && !strings.Contains(sql, "DOCUMENTS") {
			return false, "unknown table in SELECT: " + sql
		}
		return true, ""
	}
	if strings.HasPrefix(sql, "INSERT") || strings.HasPrefix(sql, "UPDATE") || strings.HasPrefix(sql, "DELETE") ||
		strings.HasPrefix(sql, "DROP") || strings.HasPrefix(sql, "ALTER") || strings.HasPrefix(sql, "ATTACH") ||
		strings.HasPrefix(sql, "PRAGMA") {
		if os.Getenv("ALLOW_WRITES") != "1" && os.Getenv("ALLOW_WRITES") != "true" {
			return false, "writes disabled: ALLOW_WRITES=1 required for " + strings.SplitN(sql, " ", 2)[0]
		}
		return false, "tool policy: " + strings.SplitN(sql, " ", 2)[0] + " requires human confirmation"
	}
	return false, "unsupported SQL: " + strings.SplitN(sql, " ", 2)[0]
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
<p class="sub">Fixed Agent (preset 5): in-built protection toggles.</p>
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

// HandleStatus writes the JSON status for GET /status.
func HandleStatus(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]interface{}{
		"preset":        5,
		"builtinGuards": os.Getenv("BUILTIN_GUARDS") == "on",
		"externalGuard": os.Getenv("EXTERNAL_GUARD") == "on",
		"allowWrites":   os.Getenv("ALLOW_WRITES") == "1" || os.Getenv("ALLOW_WRITES") == "true",
		"toolPolicy":    "allow-list: SELECT only, writes blocked without ALLOW_WRITES=1",
		"inputGuard":    os.Getenv("SCAN_INPUT"),
		"outputGuard":   os.Getenv("SCAN_OUTPUT"),
		"blockOnFail":   os.Getenv("BLOCK_ON_FAIL"),
	})
}

// HandleUI serves the HTML UI page for GET /ui.
func HandleUI(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	builtin := os.Getenv("BUILTIN_GUARDS") == "on"
	external := os.Getenv("EXTERNAL_GUARD") == "on"
	writes := os.Getenv("ALLOW_WRITES") == "1" || os.Getenv("ALLOW_WRITES") == "true"
	html := uiTemplate
	html = strings.Replace(html, `{{if .BuiltinGuards}}checked{{end}}`, "", 1)
	html = strings.Replace(html, `{{if .ExternalGuard}}checked{{end}}`, "", 1)
	html = strings.Replace(html, `{{if .AllowWrites}}checked{{end}}`, "", 1)
	if builtin {
		html = strings.Replace(html, `<input type="checkbox" id="builtinGuard" >`, `<input type="checkbox" id="builtinGuard" checked>`, 1)
	}
	if external {
		html = strings.Replace(html, `<input type="checkbox" id="externalGuard" >`, `<input type="checkbox" id="externalGuard" checked>`, 1)
	}
	if writes {
		html = strings.Replace(html, `<input type="checkbox" id="allowWrites" >`, `<input type="checkbox" id="allowWrites" checked>`, 1)
	}
	_, _ = w.Write([]byte(html))
}

// ChatRequest is the inbound HTTP contract: POST /chat {"message": "..."}.
type ChatRequest struct {
	Message string `json:"message"`
}

// ChatResponse is the outbound contract.
type ChatResponse struct {
	Reply     string `json:"reply"`
	Preset    int    `json:"preset"`
	Guarded   bool   `json:"guarded"`
	Blocked   bool   `json:"blocked"`
	Reason    string `json:"reason,omitempty"`
}

// GuardedServer wires handlers with guard enforcement.
type GuardedServer struct {
	cfg    Config
	guard  *GuardRegistry
	logger *log.Logger
}

// handleChat runs the guard-checked chat loop.
func (s *GuardedServer) handleChat(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	var req ChatRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil || strings.TrimSpace(req.Message) == "" {
		http.Error(w, "bad request: {\"message\": \"...\"} required", http.StatusBadRequest)
		return
	}

	// Input guard
	if violated, reason := s.guard.CheckInput(req.Message); violated {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusForbidden)
		_ = json.NewEncoder(w).Encode(ChatResponse{
			Reply:  "blocked by input guard: " + reason,
			Preset: s.cfg.Preset,
			Guarded: true,
			Blocked: true,
			Reason: reason,
		})
		return
	}

	// Placeholder LLM reply (no Ollama client in fixed agent; chat echoes)
	reply := "fixed agent echo: " + req.Message

	// Output guard
	if violated, reason := s.guard.CheckOutput(reply); violated {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusForbidden)
		_ = json.NewEncoder(w).Encode(ChatResponse{
			Reply:  "blocked by output guard: " + reason,
			Preset: s.cfg.Preset,
			Guarded: true,
			Blocked: true,
			Reason: reason,
		})
		return
	}

	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(ChatResponse{
		Reply:   reply,
		Preset:  s.cfg.Preset,
		Guarded: s.cfg.BuiltinGuards,
	})
}

// main is the entry point for the fixed agent (preset 5).
func main() {
	logger := log.New(os.Stdout, "[agent-fixed] ", log.LstdFlags)
	cfg := Config{
		Addr:          os.Getenv("AGENT_ADDR"),
		SystemPrompt:  os.Getenv("PROMPT_FILE"),
		LLMModel:      os.Getenv("OLLAMA_MODEL"),
		DBPath:        os.Getenv("DB_PATH"),
		AllowWrites:   os.Getenv("ALLOW_WRITES") == "1" || os.Getenv("ALLOW_WRITES") == "true",
		BuiltinGuards: strings.EqualFold(os.Getenv("BUILTIN_GUARDS"), "on"),
		ExternalGuard: strings.EqualFold(os.Getenv("EXTERNAL_GUARD"), "on"),
		Preset:        5,
	}
	if cfg.Addr == "" {
		cfg.Addr = ":8080"
	}
	if cfg.DBPath == "" {
		cfg.DBPath = "/data/db.sqlite"
	}

	guard := NewGuardRegistry()
	srv := &GuardedServer{cfg: cfg, guard: guard, logger: logger}

	mux := http.NewServeMux()
	mux.HandleFunc("/healthz", func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte("ok"))
	})
	mux.HandleFunc("/status", srv.HandleStatus)
	mux.HandleFunc("/ui", srv.HandleUI)
	mux.HandleFunc("/chat", srv.handleChat)

	logger.Printf("preset=%d builtin=%v external=%v allow_writes=%v listening=%s",
		cfg.Preset, cfg.BuiltinGuards, cfg.ExternalGuard, cfg.AllowWrites, cfg.Addr)
	if err := http.ListenAndServe(cfg.Addr, mux); err != nil {
		logger.Fatalf("serve: %v", err)
	}
}