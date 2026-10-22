package main

import (
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log"
	"net/http"
	"net/url"
	"os"
	"sort"
	"strings"
	"time"
)

type Agent struct {
	ID          string `json:"id"`
	Name        string `json:"name"`
	Description string `json:"description"`
	BaseURL     string `json:"-"`
}

type Registry map[string]Agent

func displayName(id string) string {
	switch id {
	case "baseline":
		return "Baseline (presets 1-4)"
	case "fixed":
		return "Fixed (preset 5)"
	default:
		return id
	}
}

func displayDescription(id string) string {
	switch id {
	case "baseline":
		return "Attack target with runtime presets 1-4."
	case "fixed":
		return "Immutable hardened agent with read-only tools."
	default:
		return "Registered agent."
	}
}

func parseRegistry(raw string) (Registry, error) {
	raw = strings.TrimSpace(raw)
	if raw == "" {
		return nil, errors.New("AGENT_REGISTRY is empty")
	}
	reg := Registry{}
	for _, item := range strings.Split(raw, ",") {
		item = strings.TrimSpace(item)
		if item == "" {
			continue
		}
		kv := strings.SplitN(item, "=", 2)
		if len(kv) != 2 {
			return nil, fmt.Errorf("invalid registry entry %q", item)
		}
		id := strings.TrimSpace(kv[0])
		base := strings.TrimSpace(kv[1])
		if id == "" || base == "" {
			return nil, fmt.Errorf("invalid registry entry %q", item)
		}
		if _, exists := reg[id]; exists {
			return nil, fmt.Errorf("duplicate agent id %q", id)
		}
		u, err := url.Parse(base)
		if err != nil || (u.Scheme != "http" && u.Scheme != "https") || u.Host == "" {
			return nil, fmt.Errorf("agent %q has invalid URL", id)
		}
		reg[id] = Agent{
			ID:          id,
			Name:        displayName(id),
			Description: displayDescription(id),
			BaseURL:     strings.TrimRight(base, "/"),
		}
	}
	if len(reg) == 0 {
		return nil, errors.New("AGENT_REGISTRY has no agents")
	}
	return reg, nil
}

type Server struct {
	registry Registry
	client   *http.Client
	log      *log.Logger
}

func NewServer(reg Registry, logger *log.Logger) *Server {
	if logger == nil {
		logger = log.Default()
	}
	return &Server{
		registry: reg,
		client:   &http.Client{Timeout: 180 * time.Second},
		log:      logger,
	}
}

func (s *Server) Mux() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("/healthz", func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte("ok"))
	})
	mux.HandleFunc("/api/agents", s.handleAgents)
	mux.HandleFunc("/api/agents/", s.handleAgentProxy)
	mux.HandleFunc("/", s.handleUI)
	return mux
}

func (s *Server) handleAgents(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	ids := make([]string, 0, len(s.registry))
	for id := range s.registry {
		ids = append(ids, id)
	}
	sort.Strings(ids)
	out := make([]Agent, 0, len(ids))
	for _, id := range ids {
		out = append(out, s.registry[id])
	}
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(out)
}

func (s *Server) handleAgentProxy(w http.ResponseWriter, r *http.Request) {
	rest := strings.TrimPrefix(r.URL.Path, "/api/agents/")
	parts := strings.SplitN(rest, "/", 2)
	if len(parts) != 2 || parts[0] == "" || parts[1] == "" {
		http.NotFound(w, r)
		return
	}
	agent, ok := s.registry[parts[0]]
	if !ok {
		http.NotFound(w, r)
		return
	}
	action := parts[1]
	var wantMethod string
	switch action {
	case "status":
		wantMethod = http.MethodGet
	case "settings", "chat":
		wantMethod = http.MethodPost
	default:
		http.NotFound(w, r)
		return
	}
	if r.Method != wantMethod {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	if wantMethod == http.MethodPost {
		r.Body = http.MaxBytesReader(w, r.Body, 1<<20)
	}
	s.proxy(w, r, agent, action)
}

func (s *Server) proxy(w http.ResponseWriter, r *http.Request, agent Agent, action string) {
	target := agent.BaseURL + "/" + action
	req, err := http.NewRequestWithContext(r.Context(), r.Method, target, r.Body)
	if err != nil {
		http.Error(w, "bad gateway: build request", http.StatusBadGateway)
		return
	}
	if ct := r.Header.Get("Content-Type"); ct != "" {
		req.Header.Set("Content-Type", ct)
	}
	if action == "settings" {
		req.Header.Set("X-Settings-Source", "ui")
	}
	resp, err := s.client.Do(req)
	if err != nil {
		s.log.Printf("proxy %s %s: %v", agent.ID, action, err)
		http.Error(w, "bad gateway: agent unavailable", http.StatusBadGateway)
		return
	}
	defer resp.Body.Close()
	if ct := resp.Header.Get("Content-Type"); ct != "" {
		w.Header().Set("Content-Type", ct)
	}
	w.WriteHeader(resp.StatusCode)
	_, _ = io.Copy(w, resp.Body)
}

func (s *Server) handleUI(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet || (r.URL.Path != "/" && r.URL.Path != "/ui") {
		http.NotFound(w, r)
		return
	}
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	_, _ = w.Write([]byte(uiHTML))
}

func env(key, def string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return def
}

func main() {
	logger := log.New(os.Stdout, "[ui] ", log.LstdFlags)
	reg, err := parseRegistry(env("AGENT_REGISTRY", "baseline=http://agent:8080,fixed=http://agent-fixed:8080"))
	if err != nil {
		logger.Fatalf("registry: %v", err)
	}
	addr := env("UI_ADDR", ":8090")
	srv := &http.Server{
		Addr:              addr,
		Handler:           NewServer(reg, logger).Mux(),
		ReadHeaderTimeout: 5 * time.Second,
	}
	logger.Printf("listening on %s agents=%d", addr, len(reg))
	if err := srv.ListenAndServe(); err != nil {
		logger.Fatalf("serve: %v", err)
	}
}

const uiHTML = `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Agent Security UI</title>
<style>
body { font-family: Arial, sans-serif; max-width: 960px; margin: 2rem auto; padding: 0 1rem; }
h1 { color: #1a73e8; }
.toolbar { display: flex; gap: .75rem; align-items: center; margin-bottom: 1rem; }
select { padding: .35rem; }
.grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 1rem; }
.card { border: 1px solid #ddd; border-radius: 8px; padding: 1rem; }
.card h2 { margin-top: 0; color: #333; }
.switch { position: relative; display: inline-block; width: 48px; height: 24px; vertical-align: middle; }
.switch input { opacity: 0; width: 0; height: 0; }
.slider { position: absolute; cursor: pointer; top: 0; left: 0; right: 0; bottom: 0; background-color: #ccc; transition: .3s; border-radius: 24px; }
.slider:before { position: absolute; content: ""; height: 18px; width: 18px; left: 3px; bottom: 3px; background-color: white; transition: .3s; border-radius: 50%; }
.switch input:checked + .slider { background-color: #2196F3; }
.switch input:checked + .slider:before { transform: translateX(24px); }
.switch input:disabled + .slider { opacity: .45; cursor: not-allowed; }
.badge { display: inline-block; padding: 0.15rem 0.5rem; border-radius: 4px; background: #e8f0fe; color: #1a73e8; font-weight: bold; }
.warn { color: #b26a00; background: #fff4e5; border: 1px solid #ffd8a8; padding: 0.5rem; border-radius: 4px; }
.error { color: #b00020; min-height: 1.2rem; margin-top: 0.5rem; }
pre { background:#f5f5f5; padding:0.5rem; border-radius:4px; overflow-x:auto; }
</style>
</head>
<body>
<h1>Agent Security UI</h1>
<div class="toolbar">
  <label for="agentSelect">Agent:</label>
  <select id="agentSelect"></select>
  <span>Preset: <span class="badge" id="preset">-</span></span>
  <span id="runtimeNote" class="warn" style="display:none;"></span>
</div>
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
<pre id="status">Loading...</pre>
</div>
<div class="card">
<h3>Ask the agent</h3>
<textarea id="chatInput" rows="4" style="width:100%; box-sizing:border-box;" placeholder="Type your question... (Enter to send, Shift+Enter for newline)"></textarea>
<button id="sendBtn">Send</button>
<div id="chatOutput" style="margin-top:0.5rem; white-space:pre-wrap; background:#fafafa; padding:0.5rem; border-radius:4px; min-height:1rem;"></div>
</div>
<script>
const $ = id => document.getElementById(id);
let agents = [];
let current = {};
const parse = async r => {
  const text = await r.text();
  try { return JSON.parse(text); } catch (e) { return {error: text}; }
};
const agentId = () => $('agentSelect').value;
const api = suffix => '/api/agents/' + encodeURIComponent(agentId()) + suffix;
const setSwitches = disabled => {
  ['builtinGuard', 'externalGuard', 'allowWrites'].forEach(id => $(id).disabled = disabled);
};
const refresh = async () => {
  if (!agentId()) return;
  const r = await fetch(api('/status'));
  const j = await parse(r);
  current = j;
  if (!r.ok) {
    $('status').textContent = JSON.stringify(j, null, 2);
    return;
  }
  $('builtinGuard').checked = !!j.builtinGuards;
  $('externalGuard').checked = !!j.externalGuard;
  $('allowWrites').checked = !!j.allowWrites;
  $('preset').textContent = j.preset || '-';
  const immutable = j.runtimeToggles === false;
  $('runtimeNote').style.display = immutable ? '' : 'none';
  $('runtimeNote').textContent = immutable ? 'policy immutable: settings disabled' : '';
  setSwitches(immutable);
  $('status').textContent = JSON.stringify(j, null, 2);
};
const loadAgents = async () => {
  const r = await fetch('/api/agents');
  agents = await parse(r);
  const select = $('agentSelect');
  select.innerHTML = '';
  for (const a of agents) {
    const o = document.createElement('option');
    o.value = a.id;
    o.textContent = a.name;
    o.title = a.description;
    select.appendChild(o);
  }
  await refresh();
};
const update = async () => {
  if (current.runtimeToggles === false) return;
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
  const r = await fetch(api('/settings'), {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(desired)
  });
  const j = await parse(r);
  $('settingsError').textContent = r.ok ? '' : 'HTTP ' + r.status + ': ' + (j.error || 'settings update failed');
  await refresh();
};
['builtinGuard', 'externalGuard', 'allowWrites'].forEach(id => $(id).addEventListener('change', update));
$('agentSelect').addEventListener('change', refresh);
const sendChat = async () => {
  const msg = $('chatInput').value.trim();
  if (!msg) return;
  $('chatInput').value = '';
  $('sendBtn').disabled = true;
  $('chatOutput').textContent = 'You:\n' + msg + '\n\nAgent:\nThinking...';
  try {
    const r = await fetch(api('/chat'), {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({message: msg})
    });
    const j = await parse(r);
    const answer = j.reply || j.error || JSON.stringify(j, null, 2);
    $('chatOutput').textContent = 'You:\n' + msg + '\n\nAgent:\n' + answer;
  } catch (e) {
    $('chatOutput').textContent = 'You:\n' + msg + '\n\nAgent:\nRequest failed: ' + e;
  } finally {
    $('sendBtn').disabled = false;
    $('chatInput').focus();
  }
};
$('sendBtn').onclick = sendChat;
$('chatInput').addEventListener('keydown', e => {
  if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
    e.preventDefault();
    sendChat();
  }
});
loadAgents();
</script>
</body>
</html>`
