package agentcore

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"log"
	"net/http"
	"strings"
	"sync"
)

type ChatRequest struct {
	Message string `json:"message"`
}

type ToolCall struct {
	SQL  string `json:"sql"`
	Rows int    `json:"rows"`
}

type ChatResponse struct {
	Reply     string     `json:"reply"`
	Agent     string     `json:"agent"`
	Preset    int        `json:"preset"`
	ToolCalls []ToolCall `json:"tool_calls"`
}

type Mode struct {
	AgentName       string
	Fixed           bool
	InitialSettings RuntimeSettings
	ValidateInput   func(string) error
	ValidateOutput  func(string) error
	ValidateSQL     func(string, bool) error
}

type Engine struct {
	cfg      Config
	mode     Mode
	mu       sync.RWMutex
	settings RuntimeSettings
	db       *sql.DB
	llm      *OllamaClient
	wall     *WallClient
	audit    *AuditLogger
	log      *log.Logger
}

func NewEngine(cfg Config, mode Mode, db *sql.DB, audit *AuditLogger, logger *log.Logger) *Engine {
	if cfg.AgentName == "" {
		cfg.AgentName = mode.AgentName
	}
	if mode.AgentName == "" {
		mode.AgentName = cfg.AgentName
	}
	if logger == nil {
		logger = log.Default()
	}
	return &Engine{
		cfg:      cfg,
		mode:     mode,
		settings: mode.InitialSettings,
		db:       db,
		llm:      NewOllamaClient(cfg.LLMBaseURL, cfg.LLMModel),
		wall:     NewWallClient(cfg.WallBaseURL, cfg.WallTimeout),
		audit:    audit,
		log:      logger,
	}
}

func (e *Engine) Snapshot() RuntimeSettings {
	e.mu.RLock()
	defer e.mu.RUnlock()
	return e.settings
}

func (e *Engine) fixed() bool {
	return e.cfg.Fixed || e.mode.Fixed
}

func (e *Engine) preset(s RuntimeSettings) int {
	if e.fixed() {
		return 5
	}
	return PresetOf(s)
}

func (e *Engine) Mux() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("/healthz", func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte("ok"))
	})
	mux.HandleFunc("/status", e.HandleStatus)
	mux.HandleFunc("/settings", e.HandleSettings)
	mux.HandleFunc("/chat", e.HandleChat)
	return mux
}

func (e *Engine) HandleChat(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	var req ChatRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil || strings.TrimSpace(req.Message) == "" {
		http.Error(w, "bad request: {\"message\": \"...\"} required", http.StatusBadRequest)
		return
	}
	settings := e.Snapshot()
	if settings.ExternalGuard {
		if e.wall == nil {
			http.Error(w, "external wall unavailable: wall client is not configured", http.StatusServiceUnavailable)
			return
		}
		res, err := e.wall.Validate(r.Context(), e.cfg.InputGuardName, req.Message)
		if err != nil {
			e.log.Printf("input wall error: %v", err)
			e.writeWallError(w, err)
			return
		}
		if !res.Passed {
			e.writeBlocked(w, "input", res)
			return
		}
		req.Message = res.Validated
	}
	if e.mode.ValidateInput != nil {
		if err := e.mode.ValidateInput(req.Message); err != nil {
			e.writeGuardBlocked(w, "input", err)
			return
		}
	}

	reply, calls, err := e.runToolLoop(r.Context(), req.Message, settings)
	if err != nil {
		e.log.Printf("chat error: %v", err)
		http.Error(w, "llm/db error: "+err.Error(), http.StatusBadGateway)
		return
	}

	if settings.ExternalGuard {
		if e.wall == nil {
			http.Error(w, "external wall unavailable: wall client is not configured", http.StatusServiceUnavailable)
			return
		}
		res, err := e.wall.Validate(r.Context(), e.cfg.OutputGuardName, reply)
		if err != nil {
			e.log.Printf("output wall error: %v", err)
			e.writeWallError(w, err)
			return
		}
		changed := res.Validated != res.Raw
		if !res.Passed || changed {
			if e.cfg.BlockOnFail {
				e.writeBlocked(w, "output", res)
				return
			}
		}
		if res.Validated != "" {
			reply = res.Validated
		}
	}
	if e.mode.ValidateOutput != nil {
		if err := e.mode.ValidateOutput(reply); err != nil {
			e.writeGuardBlocked(w, "output", err)
			return
		}
	}

	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(ChatResponse{
		Reply:     reply,
		Agent:     e.cfg.AgentName,
		Preset:    e.preset(settings),
		ToolCalls: calls,
	})
}

func (e *Engine) runToolLoop(ctx context.Context, userMsg string, settings RuntimeSettings) (string, []ToolCall, error) {
	var calls []ToolCall
	prompt := e.cfg.PromptFor(settings)
	guarded := e.cfg.EffectiveGuarded(settings)
	reply, err := e.llm.Chat(ctx, prompt, userMsg)
	if err != nil {
		return "", calls, err
	}
	for i := 0; i < MaxToolIterations; i++ {
		stmt := ExtractSQL(reply)
		if stmt == "" {
			return reply, calls, nil
		}
		if e.mode.ValidateSQL != nil {
			if err := e.mode.ValidateSQL(stmt, settings.AllowWrites); err != nil {
				follow, ferr := e.llm.Chat(ctx, prompt,
					ToolFollowup(userMsg, stmt, "", 0, err, guarded))
				if ferr != nil {
					return "", calls, ferr
				}
				return follow, calls, nil
			}
		}
		result, n, err := QueryDB(ctx, e.db, stmt, settings.AllowWrites)
		if err != nil {
			follow, ferr := e.llm.Chat(ctx, prompt,
				ToolFollowup(userMsg, stmt, "", 0, err, guarded))
			if ferr != nil {
				return "", calls, ferr
			}
			return follow, calls, nil
		}
		calls = append(calls, ToolCall{SQL: stmt, Rows: n})
		follow, err := e.llm.Chat(ctx, prompt,
			ToolFollowup(userMsg, stmt, result, n, nil, guarded))
		if err != nil {
			return "", calls, err
		}
		reply = follow
	}
	return reply, calls, nil
}

func (e *Engine) StatusPayload(settings RuntimeSettings) map[string]interface{} {
	toolPolicy := "allow-list: SELECT only, writes blocked without ALLOW_WRITES=1"
	if e.fixed() {
		toolPolicy = "allow-list: SELECT only on known tables; writes always blocked"
	} else if settings.AllowWrites {
		toolPolicy = "writes enabled for this session; restore the DB after destructive runs"
	}
	return map[string]interface{}{
		"agent":          e.cfg.AgentName,
		"preset":         e.preset(settings),
		"builtinGuards":  settings.BuiltinGuards,
		"externalGuard":  settings.ExternalGuard,
		"allowWrites":    settings.AllowWrites,
		"runtimeToggles": e.cfg.RuntimeToggles(),
		"immutable":      e.fixed(),
		"toolPolicy":     toolPolicy,
	}
}

func (e *Engine) HandleStatus(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(e.StatusPayload(e.Snapshot()))
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

func (e *Engine) auditRejected(source, result string) {
	if e.audit == nil {
		return
	}
	cur := e.Snapshot()
	if err := e.audit.Log(AuditEvent{
		Event:  "settings.update",
		Source: source,
		Old:    cur,
		New:    cur,
		Preset: e.preset(cur),
		Result: result,
	}); err != nil {
		e.log.Printf("audit rejected settings update: %v", err)
	}
}

func (e *Engine) HandleSettings(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	source := settingsSource(r)
	if e.fixed() {
		e.auditRejected(source, "forbidden: fixed agent settings are immutable")
		http.Error(w, "fixed agent settings are immutable", http.StatusForbidden)
		return
	}
	if !e.cfg.RuntimeToggles() {
		e.auditRejected(source, "forbidden: runtime toggles disabled")
		http.Error(w, "runtime toggles disabled (ALLOW_RUNTIME_TOGGLES!=on)", http.StatusForbidden)
		return
	}

	r.Body = http.MaxBytesReader(w, r.Body, 4096)
	dec := json.NewDecoder(r.Body)
	dec.DisallowUnknownFields()
	var req SettingsRequest
	if err := dec.Decode(&req); err != nil {
		e.auditRejected(source, "rejected: invalid JSON")
		http.Error(w, "invalid settings JSON: "+err.Error(), http.StatusBadRequest)
		return
	}
	if req.BuiltinGuards == nil || req.ExternalGuard == nil || req.AllowWrites == nil {
		e.auditRejected(source, "rejected: missing fields")
		http.Error(w, "builtinGuards, externalGuard and allowWrites are required", http.StatusBadRequest)
		return
	}
	if *req.AllowWrites && !req.ConfirmWrites {
		e.auditRejected(source, "rejected: confirmWrites required")
		http.Error(w, "confirmWrites required", http.StatusBadRequest)
		return
	}

	next := RuntimeSettings{
		BuiltinGuards: *req.BuiltinGuards,
		ExternalGuard: *req.ExternalGuard,
		AllowWrites:   *req.AllowWrites,
	}

	if e.audit == nil {
		http.Error(w, "audit log unavailable", http.StatusInternalServerError)
		return
	}
	e.mu.Lock()
	old := e.settings
	ev := AuditEvent{
		Event:  "settings.update",
		Source: source,
		Old:    old,
		New:    next,
		Preset: e.preset(next),
		Result: "ok",
	}
	if err := e.audit.Log(ev); err != nil {
		e.mu.Unlock()
		e.log.Printf("audit settings update: %v", err)
		http.Error(w, "audit log unavailable", http.StatusInternalServerError)
		return
	}
	e.settings = next
	e.mu.Unlock()

	e.log.Printf("settings updated source=%s preset=%d builtin=%v external=%v allow_writes=%v",
		source, ev.Preset, next.BuiltinGuards, next.ExternalGuard, next.AllowWrites)

	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(e.StatusPayload(next))
}

func (e *Engine) writeBlocked(w http.ResponseWriter, stage string, res WallResult) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusForbidden)
	_ = json.NewEncoder(w).Encode(map[string]string{
		"error":  "blocked by external wall",
		"stage":  stage,
		"reason": "validation failed",
	})
}

func (e *Engine) writeGuardBlocked(w http.ResponseWriter, stage string, err error) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusForbidden)
	_ = json.NewEncoder(w).Encode(map[string]string{
		"error":  "blocked by builtin guard",
		"stage":  stage,
		"reason": err.Error(),
	})
}

func (e *Engine) writeWallError(w http.ResponseWriter, err error) {
	status := http.StatusBadGateway
	if errors.Is(err, ErrWallUnavailable) {
		status = http.StatusServiceUnavailable
	}
	http.Error(w, "external wall unavailable: "+err.Error(), status)
}
