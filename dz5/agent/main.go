// Package main implements the dz5 stand agent (Go).
//
// Presets 1-4: a minimal HTTP agent that talks to the local abliterated LLM
// target and reads the SQLite stand DB through the db_query tool. Builtin
// guards select the system prompt (BUILTIN_GUARDS); the external Guardrails
// Server validates input/output (EXTERNAL_GUARD) without proxying LLM traffic.
package main

import (
	"database/sql"
	"encoding/json"
	"errors"
	"log"
	"net/http"
	"os"
	"strings"
	"sync"
	"time"

	_ "modernc.org/sqlite"
)

// Config holds the static configuration of the stand agent. The mutable
// protection matrix (presets 1-4) lives in RuntimeSettings behind a mutex.
type Config struct {
	Addr          string // :8080
	MinimalPrompt string // prompts/minimal.txt (BUILTIN_GUARDS=off)
	GuardedPrompt string // prompts/guarded.txt (BUILTIN_GUARDS=on)
	LLMBaseURL    string // always OLLAMA_BASE_URL; the wall is not an LLM proxy
	LLMModel      string // OLLAMA_MODEL
	DBPath        string // /data/db.sqlite

	WallBaseURL     string        // GUARDRAILS_BASE_URL (http://wall:8000)
	InputGuardName  string        // INPUT_GUARD_NAME
	OutputGuardName string        // OUTPUT_GUARD_NAME
	WallTimeout     time.Duration // GUARDRAILS_TIMEOUT
	BlockOnFail     bool          // BLOCK_ON_FAIL=on blocks output violations

	AllowRuntimeToggles bool   // ALLOW_RUNTIME_TOGGLES=on enables POST /settings
	AuditLog            string // AUDIT_LOG (default /data/audit.jsonl)
}

// promptFor selects the system prompt for a runtime snapshot.
func (c Config) promptFor(s RuntimeSettings) string {
	if s.BuiltinGuards {
		return c.GuardedPrompt
	}
	return c.MinimalPrompt
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
	cfg      Config
	mu       sync.RWMutex
	settings RuntimeSettings
	db       *sql.DB
	llm      *OllamaClient
	wall     *WallClient
	audit    *AuditLogger
	log      *log.Logger
}

// snapshot returns one consistent settings view for a request.
func (s *Server) snapshot() RuntimeSettings {
	s.mu.RLock()
	defer s.mu.RUnlock()
	return s.settings
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

func loadConfig(minimalPrompt, guardedPrompt string) Config {
	return Config{
		Addr:                getenv("AGENT_ADDR", ":8080"),
		MinimalPrompt:       minimalPrompt,
		GuardedPrompt:       guardedPrompt,
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
	}
}

func settingsFromEnv() RuntimeSettings {
	truthy := func(v string) bool {
		return strings.EqualFold(v, "on") || v == "1" || strings.EqualFold(v, "true")
	}
	return RuntimeSettings{
		BuiltinGuards: truthy(getenv("BUILTIN_GUARDS", "off")),
		ExternalGuard: truthy(getenv("EXTERNAL_GUARD", "off")),
		AllowWrites:   truthy(getenv("ALLOW_WRITES", "off")),
	}
}

func main() {
	logger := log.New(os.Stdout, "[agent] ", log.LstdFlags)
	minimal, guarded, err := loadPrompts()
	if err != nil {
		logger.Fatalf("load system prompts: %v", err)
	}
	cfg := loadConfig(minimal, guarded)
	settings := settingsFromEnv()
	logger.Printf("preset=%d builtin=%v external=%v allow_writes=%v runtime_toggles=%v llm=%s model=%s wall=%s",
		presetOf(settings), settings.BuiltinGuards, settings.ExternalGuard, settings.AllowWrites,
		cfg.AllowRuntimeToggles, cfg.LLMBaseURL, cfg.LLMModel, cfg.WallBaseURL)

	db, err := sql.Open("sqlite", cfg.DBPath)
	if err != nil {
		logger.Fatalf("open db %s: %v", cfg.DBPath, err)
	}
	defer db.Close()
	if err := db.Ping(); err != nil {
		logger.Fatalf("ping db %s: %v", cfg.DBPath, err)
	}

	audit, err := NewAuditLogger(cfg.AuditLog)
	if err != nil {
		logger.Fatalf("audit log: %v", err)
	}

	srv := &Server{
		cfg:      cfg,
		settings: settings,
		db:       db,
		llm:      NewOllamaClient(cfg.LLMBaseURL, cfg.LLMModel),
		wall:     NewWallClient(cfg.WallBaseURL, cfg.WallTimeout),
		audit:    audit,
		log:      logger,
	}
	mux := http.NewServeMux()
	mux.HandleFunc("/healthz", func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte("ok"))
	})
	mux.HandleFunc("/status", srv.handleStatus)
	mux.HandleFunc("/settings", srv.handleSettings)
	mux.HandleFunc("/ui", srv.handleUI)
	mux.HandleFunc("/chat", srv.handleChat)
	logger.Printf("listening on %s", cfg.Addr)
	if err := http.ListenAndServe(cfg.Addr, mux); err != nil {
		logger.Fatalf("serve: %v", err)
	}
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
	settings := s.snapshot()
	if settings.ExternalGuard {
		if s.wall == nil {
			http.Error(w, "external wall unavailable: wall client is not configured", http.StatusServiceUnavailable)
			return
		}
		res, err := s.wall.Validate(r.Context(), s.cfg.InputGuardName, req.Message)
		if err != nil {
			s.log.Printf("input wall error: %v", err)
			s.writeWallError(w, err)
			return
		}
		if !res.Passed {
			s.writeBlocked(w, "input", res)
			return
		}
		req.Message = res.Validated
	}

	reply, calls, err := s.runToolLoop(r.Context(), req.Message, settings)
	if err != nil {
		s.log.Printf("chat error: %v", err)
		http.Error(w, "llm/db error: "+err.Error(), http.StatusBadGateway)
		return
	}

	if settings.ExternalGuard {
		if s.wall == nil {
			http.Error(w, "external wall unavailable: wall client is not configured", http.StatusServiceUnavailable)
			return
		}
		res, err := s.wall.Validate(r.Context(), s.cfg.OutputGuardName, reply)
		if err != nil {
			s.log.Printf("output wall error: %v", err)
			s.writeWallError(w, err)
			return
		}
		changed := res.Validated != res.Raw
		if !res.Passed || changed {
			if s.cfg.BlockOnFail {
				s.writeBlocked(w, "output", res)
				return
			}
		}
		if res.Validated != "" {
			reply = res.Validated
		}
	}

	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(ChatResponse{Reply: reply, Preset: presetOf(settings), ToolCalls: calls})
}

// writeBlocked returns 403 for an external-wall policy violation.
func (s *Server) writeBlocked(w http.ResponseWriter, stage string, res WallResult) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusForbidden)
	_ = json.NewEncoder(w).Encode(map[string]string{
		"error":  "blocked by external wall",
		"stage":  stage,
		"reason": "validation failed",
	})
}

// writeWallError maps wall transport/5xx failures to fail-closed 503.
func (s *Server) writeWallError(w http.ResponseWriter, err error) {
	status := http.StatusBadGateway
	if errors.Is(err, ErrWallUnavailable) {
		status = http.StatusServiceUnavailable
	}
	http.Error(w, "external wall unavailable: "+err.Error(), status)
}

// statusPayload renders the current state for /status and /settings.
func (s *Server) statusPayload(settings RuntimeSettings) map[string]interface{} {
	toolPolicy := "allow-list: SELECT only, writes blocked without ALLOW_WRITES=1"
	if settings.AllowWrites {
		toolPolicy = "writes enabled for this session; restore the DB after destructive runs"
	}
	return map[string]interface{}{
		"preset":            presetOf(settings),
		"builtinGuards":     settings.BuiltinGuards,
		"externalGuard":     settings.ExternalGuard,
		"allowWrites":       settings.AllowWrites,
		"runtimeToggles":    s.cfg.AllowRuntimeToggles,
		"toolPolicy":        toolPolicy,
		"llmBaseURL":        s.cfg.LLMBaseURL,
		"guardrailsBaseURL": s.cfg.WallBaseURL,
	}
}

// handleStatus returns the current runtime protection state.
func (s *Server) handleStatus(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(s.statusPayload(s.snapshot()))
}

func settingsSource(r *http.Request) string {
	source := strings.TrimSpace(r.Header.Get("X-Settings-Source"))
	if source == "" {
		source = "api"
	}
	if len(source) > 32 {
		source = source[:32]
	}
	return source
}

// auditRejected records refused settings attempts without leaking request data.
func (s *Server) auditRejected(source, result string) {
	if s.audit == nil {
		return
	}
	cur := s.snapshot()
	if err := s.audit.Log(AuditEvent{
		Event:  "settings.update",
		Source: source,
		Old:    cur,
		New:    cur,
		Preset: presetOf(cur),
		Result: result,
	}); err != nil {
		s.log.Printf("audit rejected settings update: %v", err)
	}
}

// handleSettings applies a runtime preset change. It is disabled unless
// ALLOW_RUNTIME_TOGGLES=on, and every applied or refused change is audited.
func (s *Server) handleSettings(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	source := settingsSource(r)
	if !s.cfg.AllowRuntimeToggles {
		s.auditRejected(source, "forbidden: runtime toggles disabled")
		http.Error(w, "runtime toggles disabled (ALLOW_RUNTIME_TOGGLES!=on)", http.StatusForbidden)
		return
	}

	r.Body = http.MaxBytesReader(w, r.Body, 4096)
	dec := json.NewDecoder(r.Body)
	dec.DisallowUnknownFields()
	var req SettingsRequest
	if err := dec.Decode(&req); err != nil {
		s.auditRejected(source, "rejected: invalid JSON")
		http.Error(w, "invalid settings JSON: "+err.Error(), http.StatusBadRequest)
		return
	}
	if req.BuiltinGuards == nil || req.ExternalGuard == nil || req.AllowWrites == nil {
		s.auditRejected(source, "rejected: missing fields")
		http.Error(w, "builtinGuards, externalGuard and allowWrites are required", http.StatusBadRequest)
		return
	}
	if *req.AllowWrites && !req.ConfirmWrites {
		s.auditRejected(source, "rejected: confirmWrites required")
		http.Error(w, "confirmWrites required", http.StatusBadRequest)
		return
	}

	next := RuntimeSettings{
		BuiltinGuards: *req.BuiltinGuards,
		ExternalGuard: *req.ExternalGuard,
		AllowWrites:   *req.AllowWrites,
	}

	s.mu.Lock()
	old := s.settings
	ev := AuditEvent{
		Event:  "settings.update",
		Source: source,
		Old:    old,
		New:    next,
		Preset: presetOf(next),
		Result: "ok",
	}
	if err := s.audit.Log(ev); err != nil {
		s.mu.Unlock()
		s.log.Printf("audit settings update: %v", err)
		http.Error(w, "audit log unavailable", http.StatusInternalServerError)
		return
	}
	s.settings = next
	s.mu.Unlock()

	s.log.Printf("settings updated source=%s preset=%d builtin=%v external=%v allow_writes=%v",
		source, ev.Preset, next.BuiltinGuards, next.ExternalGuard, next.AllowWrites)

	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(s.statusPayload(next))
}

// uiTemplate is the HTML page for GET /ui. State comes from /status; the
// switches POST to /settings and re-read /status afterwards.
const uiTemplate = `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Agent Security UI</title>
<style>
body { font-family: Arial, sans-serif; max-width: 900px; margin: 2rem auto; padding: 0 1rem; }
h1 { color: #1a73e8; }
.grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 1rem; }
.card { border: 1px solid #ddd; border-radius: 8px; padding: 1rem; }
.card h2 { margin-top: 0; color: #333; }
.switch { position: relative; display: inline-block; width: 48px; height: 24px; vertical-align: middle; }
.switch input { opacity: 0; width: 0; height: 0; }
.slider { position: absolute; cursor: pointer; top: 0; left: 0; right: 0; bottom: 0; background-color: #ccc; transition: .3s; border-radius: 24px; }
.slider:before { position: absolute; content: ""; height: 18px; width: 18px; left: 3px; bottom: 3px; background-color: white; transition: .3s; border-radius: 50%; }
.switch input:checked + .slider { background-color: #2196F3; }
.switch input:checked + .slider:before { transform: translateX(24px); }
.badge { display: inline-block; padding: 0.15rem 0.5rem; border-radius: 4px; background: #e8f0fe; color: #1a73e8; font-weight: bold; }
.warn { color: #b26a00; background: #fff4e5; border: 1px solid #ffd8a8; padding: 0.5rem; border-radius: 4px; }
.error { color: #b00020; min-height: 1.2rem; margin-top: 0.5rem; }
</style>
</head>
<body>
<h1>Agent Security UI</h1>
<p>Current preset: <span class="badge" id="preset">-</span> <span id="runtimeNote"></span></p>
<div id="settingsError" class="error"></div>
<div class="grid">
  <div class="card">
    <h2>Builtin Guards <label class="switch"><input type="checkbox" id="builtinGuard"><span class="slider"></span></label></h2>
    <p>Prompt isolation and guarded instructions.</p>
  </div>
  <div class="card">
    <h2>External Wall <label class="switch"><input type="checkbox" id="externalGuard"><span class="slider"></span></label></h2>
    <p>Validate input/output via Guardrails Server.</p>
  </div>
  <div class="card">
    <h2>Write Tools <label class="switch"><input type="checkbox" id="allowWrites"><span class="slider"></span></label></h2>
    <p>Allow <code>INSERT/UPDATE/DELETE/DROP</code> after confirmation.</p>
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
let current = {};
const parse = async r => {
  const text = await r.text();
  try { return JSON.parse(text); } catch (e) { return {error: text}; }
};
const refresh = async () => {
  const r = await fetch('/status');
  const j = await parse(r);
  current = j;
  $('builtinGuard').checked = !!j.builtinGuards;
  $('externalGuard').checked = !!j.externalGuard;
  $('allowWrites').checked = !!j.allowWrites;
  $('preset').textContent = j.preset || '-';
  $('runtimeNote').textContent = j.runtimeToggles ? '' : '(runtime toggles disabled)';
  $('runtimeNote').className = j.runtimeToggles ? '' : 'warn';
  $('status').textContent = JSON.stringify(j, null, 2);
};
const update = async () => {
  const desired = {
    builtinGuards: $('builtinGuard').checked,
    externalGuard: $('externalGuard').checked,
    allowWrites: $('allowWrites').checked,
    confirmWrites: false
  };
  if (desired.allowWrites && !current.allowWrites) {
    if (!confirm('Enable write tools (INSERT/UPDATE/DELETE/DROP)? Restore the DB after the demo.')) {
      await refresh();
      return;
    }
  }
  desired.confirmWrites = desired.allowWrites;
  const r = await fetch('/settings', {
    method: 'POST',
    headers: {'Content-Type': 'application/json', 'X-Settings-Source': 'ui'},
    body: JSON.stringify(desired)
  });
  const j = await parse(r);
  if (!r.ok) {
    $('settingsError').textContent = 'HTTP ' + r.status + ': ' + (j.error || 'settings update failed');
  } else {
    $('settingsError').textContent = '';
  }
  await refresh();
};
['builtinGuard', 'externalGuard', 'allowWrites'].forEach(id => $(id).addEventListener('change', update));
$('sendBtn').onclick = async () => {
  const msg = $('chatInput').value;
  const r = await fetch('/chat', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({message: msg})
  });
  const j = await parse(r);
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
	_, _ = w.Write([]byte(uiTemplate))
}
