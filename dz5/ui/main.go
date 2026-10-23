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
	"path/filepath"
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

type AugustusRun struct {
	RunID           string   `json:"run_id"`
	Target          string   `json:"target,omitempty"`
	Preset          int      `json:"preset,omitempty"`
	Runs            int      `json:"runs,omitempty"`
	Status          string   `json:"status,omitempty"`
	Reason          string   `json:"reason,omitempty"`
	ReturnCode      int      `json:"returncode"`
	Probes          []string `json:"probes,omitempty"`
	DBChanged       *bool    `json:"db_changed,omitempty"`
	StartedAt       string   `json:"started_at,omitempty"`
	FinishedAt      string   `json:"finished_at,omitempty"`
	ReportAvailable bool     `json:"report_available"`
	ConfigAvailable bool     `json:"config_available"`
	ReportURL       string   `json:"report_url,omitempty"`
	ConfigURL       string   `json:"config_url,omitempty"`
}

type augustusManifest struct {
	Preset     int             `json:"preset"`
	Runs       int             `json:"runs"`
	Target     string          `json:"target"`
	StartedAt  string          `json:"started_at"`
	FinishedAt string          `json:"finished_at"`
	Augustus   *augustusResult `json:"augustus"`
}

type augustusResult struct {
	Status     string   `json:"status"`
	Reason     string   `json:"reason"`
	Target     string   `json:"target"`
	ReturnCode int      `json:"returncode"`
	Probes     []string `json:"probes"`
	DBChanged  *bool    `json:"db_changed"`
}

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
	registry   Registry
	client     *http.Client
	log        *log.Logger
	reportsDir string
}

func NewServer(reg Registry, logger *log.Logger) *Server {
	if logger == nil {
		logger = log.Default()
	}
	return &Server{
		registry:   reg,
		client:     &http.Client{Timeout: 180 * time.Second},
		log:        logger,
		reportsDir: env("REPORTS_DIR", "/reports"),
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
	mux.HandleFunc("/api/augustus/runs", s.handleAugustusRuns)
	mux.HandleFunc("/api/augustus/runs/", s.handleAugustusRunFile)
	mux.HandleFunc("/augustus", s.handleAugustusUI)
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

func (s *Server) handleAugustusRuns(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(s.listAugustusRuns())
}

func (s *Server) listAugustusRuns() []AugustusRun {
	entries, err := os.ReadDir(s.reportsDir)
	if err != nil {
		return []AugustusRun{}
	}
	runs := make([]AugustusRun, 0, len(entries))
	for _, entry := range entries {
		if !entry.IsDir() || !validRunID(entry.Name()) {
			continue
		}
		runID := entry.Name()
		runDir := filepath.Join(s.reportsDir, runID)
		augustusDir := filepath.Join(runDir, "augustus")
		configPath := filepath.Join(augustusDir, "config.yaml")
		reportPath := filepath.Join(augustusDir, "report.html")
		if !regularFile(configPath) {
			continue
		}

		run := AugustusRun{
			RunID:           runID,
			ConfigAvailable: true,
			ConfigURL:       "/api/augustus/runs/" + url.PathEscape(runID) + "/config.yaml",
		}
		if regularFile(reportPath) {
			run.ReportAvailable = true
			run.ReportURL = "/api/augustus/runs/" + url.PathEscape(runID) + "/report.html"
		}
		if data, err := os.ReadFile(filepath.Join(runDir, "manifest.json")); err == nil {
			var manifest augustusManifest
			if json.Unmarshal(data, &manifest) == nil {
				run.Preset = manifest.Preset
				run.Runs = manifest.Runs
				run.Target = manifest.Target
				run.StartedAt = manifest.StartedAt
				run.FinishedAt = manifest.FinishedAt
				if manifest.Augustus != nil {
					run.Status = manifest.Augustus.Status
					run.Reason = manifest.Augustus.Reason
					run.ReturnCode = manifest.Augustus.ReturnCode
					run.Probes = manifest.Augustus.Probes
					run.DBChanged = manifest.Augustus.DBChanged
					if manifest.Augustus.Target != "" {
						run.Target = manifest.Augustus.Target
					}
				}
			}
		}
		runs = append(runs, run)
	}
	sort.Slice(runs, func(i, j int) bool {
		return runs[i].RunID > runs[j].RunID
	})
	return runs
}

func (s *Server) handleAugustusRunFile(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	rest := strings.TrimPrefix(r.URL.Path, "/api/augustus/runs/")
	parts := strings.SplitN(rest, "/", 2)
	if len(parts) != 2 || !validRunID(parts[0]) {
		http.NotFound(w, r)
		return
	}
	contentTypes := map[string]string{
		"report.html": "text/html; charset=utf-8",
		"config.yaml": "text/plain; charset=utf-8",
	}
	contentType, ok := contentTypes[parts[1]]
	if !ok {
		http.NotFound(w, r)
		return
	}
	path := filepath.Join(s.reportsDir, parts[0], "augustus", parts[1])
	if !regularFile(path) {
		http.NotFound(w, r)
		return
	}
	w.Header().Set("Content-Type", contentType)
	w.Header().Set("X-Content-Type-Options", "nosniff")
	http.ServeFile(w, r, path)
}

func validRunID(id string) bool {
	if id == "" || id == "." || id == ".." {
		return false
	}
	for _, r := range id {
		if (r >= 'a' && r <= 'z') || (r >= 'A' && r <= 'Z') ||
			(r >= '0' && r <= '9') || r == '-' || r == '_' {
			continue
		}
		return false
	}
	return true
}

func regularFile(path string) bool {
	info, err := os.Stat(path)
	return err == nil && info.Mode().IsRegular()
}

func (s *Server) handleAugustusUI(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet || (r.URL.Path != "/augustus" && r.URL.Path != "/augustus/") {
		http.NotFound(w, r)
		return
	}
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	_, _ = w.Write([]byte(augustusHTML))
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
.tabs { display: flex; gap: .5rem; border-bottom: 1px solid #ddd; margin-bottom: 1rem; }
.tabs a { padding: .5rem .9rem; color: #555; text-decoration: none; border-radius: 6px 6px 0 0; }
.tabs a.active { background: #e8f0fe; color: #1a73e8; font-weight: bold; }
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
<nav class="tabs"><a class="active" href="/">Agents</a><a href="/augustus">Augustus</a></nav>
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

const augustusHTML = `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Augustus - Agent Security UI</title>
<style>
body { font-family: Arial, sans-serif; max-width: 1100px; margin: 2rem auto; padding: 0 1rem; }
h1 { color: #1a73e8; }
.tabs { display: flex; gap: .5rem; border-bottom: 1px solid #ddd; margin-bottom: 1rem; }
.tabs a { padding: .5rem .9rem; color: #555; text-decoration: none; border-radius: 6px 6px 0 0; }
.tabs a.active { background: #e8f0fe; color: #1a73e8; font-weight: bold; }
.toolbar { display: flex; gap: .75rem; align-items: center; margin-bottom: 1rem; flex-wrap: wrap; }
select { padding: .35rem; }
.grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 1rem; }
.card { border: 1px solid #ddd; border-radius: 8px; padding: 1rem; margin-bottom: 1rem; }
.card h2, .card h3 { margin-top: 0; color: #333; }
dl { display: grid; grid-template-columns: 130px 1fr; gap: .35rem .75rem; margin: 0; }
dt { color: #666; }
dd { margin: 0; overflow-wrap: anywhere; }
.badge { display: inline-block; padding: .15rem .5rem; border-radius: 4px; background: #e8f0fe; color: #1a73e8; font-weight: bold; }
.warn { color: #b26a00; background: #fff4e5; border: 1px solid #ffd8a8; padding: .5rem; border-radius: 4px; }
pre { background: #f5f5f5; padding: .75rem; border-radius: 4px; overflow: auto; max-height: 420px; }
iframe { width: 100%; height: 720px; border: 1px solid #ddd; border-radius: 4px; background: #fff; }
code { background: #f5f5f5; padding: .1rem .3rem; border-radius: 3px; }
</style>
</head>
<body>
<h1>Augustus</h1>
<nav class="tabs"><a href="/">Agents</a><a class="active" href="/augustus">Augustus</a></nav>
<div class="toolbar">
  <label for="runSelect">Run:</label>
  <select id="runSelect"></select>
  <span id="note" class="warn" style="display:none;"></span>
</div>
<div id="empty" class="warn" style="display:none;">
  Augustus-отчётов пока нет. Запусти attack-runner с <code>--augustus on</code>.
</div>
<div class="grid">
  <div class="card">
    <h2>Settings</h2>
    <dl>
      <dt>Target</dt><dd id="metaTarget">-</dd>
      <dt>Preset</dt><dd id="metaPreset">-</dd>
      <dt>Runs</dt><dd id="metaRuns">-</dd>
      <dt>Probes</dt><dd id="metaProbes">-</dd>
      <dt>Time</dt><dd id="metaTime">-</dd>
    </dl>
  </div>
  <div class="card">
    <h2>Result</h2>
    <dl>
      <dt>Status</dt><dd><span id="metaStatus" class="badge">-</span></dd>
      <dt>Return code</dt><dd id="metaReturn">-</dd>
      <dt>DB changed</dt><dd id="metaDB">-</dd>
    </dl>
  </div>
</div>
<div class="card">
  <h3>Effective config</h3>
  <pre id="config">-</pre>
</div>
<div class="card">
  <h3>HTML report <a id="reportLink" href="#" target="_blank" style="display:none;">open in new tab</a></h3>
  <p id="reportNote">-</p>
  <iframe id="reportFrame" style="display:none;" title="Augustus HTML report"></iframe>
</div>
<script>
const $ = id => document.getElementById(id);
let runs = [];
const show = async () => {
  const run = runs.find(item => item.run_id === $('runSelect').value);
  if (!run) return;
  $('metaTarget').textContent = run.target || '-';
  $('metaPreset').textContent = run.preset || '-';
  $('metaRuns').textContent = run.runs || '-';
  $('metaProbes').textContent = (run.probes || []).join(', ') || '-';
  $('metaTime').textContent = (run.started_at || '-') + ' / ' + (run.finished_at || '-');
  $('metaStatus').textContent = run.status || 'unknown';
  $('metaReturn').textContent = run.returncode === undefined ? '-' : run.returncode;
  $('metaDB').textContent = run.db_changed === undefined || run.db_changed === null
    ? '-' : (run.db_changed ? 'changed' : 'unchanged');
  $('note').style.display = run.reason ? '' : 'none';
  $('note').textContent = run.reason || '';

  $('config').textContent = 'Loading...';
  if (run.config_available) {
    const response = await fetch(run.config_url);
    $('config').textContent = await response.text();
  } else {
    $('config').textContent = 'config.yaml not found';
  }

  if (run.report_available) {
    $('reportNote').textContent = '';
    $('reportFrame').src = run.report_url;
    $('reportFrame').style.display = 'block';
    $('reportLink').href = run.report_url;
    $('reportLink').style.display = 'inline';
  } else {
    $('reportNote').textContent = 'report.html not found for this run.';
    $('reportFrame').style.display = 'none';
    $('reportLink').style.display = 'none';
  }
};
const load = async () => {
  const response = await fetch('/api/augustus/runs');
  runs = await response.json();
  const select = $('runSelect');
  select.innerHTML = '';
  if (!runs.length) {
    $('empty').style.display = '';
    return;
  }
  for (const run of runs) {
    const option = document.createElement('option');
    option.value = run.run_id;
    option.textContent = run.run_id + '  [' + (run.status || 'unknown') + ']';
    select.appendChild(option);
  }
  await show();
};
$('runSelect').addEventListener('change', show);
load();
</script>
</body>
</html>`
