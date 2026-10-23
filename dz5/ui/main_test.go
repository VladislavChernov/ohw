package main

import (
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestParseRegistry(t *testing.T) {
	reg, err := parseRegistry("baseline=http://agent:8080,fixed=http://agent-fixed:8080")
	if err != nil {
		t.Fatal(err)
	}
	if len(reg) != 2 || reg["fixed"].BaseURL != "http://agent-fixed:8080" {
		t.Fatalf("unexpected registry: %+v", reg)
	}
	for _, raw := range []string{"", "broken", "a=ftp://x", "a=http://x,a=http://y", "a="} {
		if _, err := parseRegistry(raw); err == nil {
			t.Fatalf("registry accepted: %q", raw)
		}
	}
}

func TestAgentsEndpoint(t *testing.T) {
	reg := Registry{
		"baseline": {ID: "baseline", Name: "Baseline (presets 1-4)", BaseURL: "http://agent:8080"},
		"fixed":    {ID: "fixed", Name: "Fixed (preset 5)", BaseURL: "http://agent-fixed:8080"},
	}
	rr := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodGet, "/api/agents", nil)
	NewServer(reg, nil).Mux().ServeHTTP(rr, req)
	if rr.Code != http.StatusOK {
		t.Fatalf("status = %d", rr.Code)
	}
	var got []Agent
	if err := json.Unmarshal(rr.Body.Bytes(), &got); err != nil {
		t.Fatal(err)
	}
	if len(got) != 2 || got[0].ID != "baseline" || got[1].ID != "fixed" {
		t.Fatalf("unexpected agents: %+v", got)
	}
	if strings.Contains(rr.Body.String(), "http://agent") {
		t.Fatal("internal URL leaked to browser")
	}
}

func TestProxyPassesSettingsAndStatus(t *testing.T) {
	var gotPath, gotSource, gotBody string
	backend := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotPath = r.URL.Path
		gotSource = r.Header.Get("X-Settings-Source")
		b, _ := io.ReadAll(r.Body)
		gotBody = string(b)
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusForbidden)
		_, _ = w.Write([]byte(`{"error":"fixed agent settings are immutable"}`))
	}))
	defer backend.Close()

	reg := Registry{"fixed": {ID: "fixed", BaseURL: backend.URL}}
	rr := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodPost, "/api/agents/fixed/settings",
		strings.NewReader(`{"builtinGuards":false,"externalGuard":false,"allowWrites":false}`))
	req.Header.Set("Content-Type", "application/json")
	NewServer(reg, nil).Mux().ServeHTTP(rr, req)

	if rr.Code != http.StatusForbidden {
		t.Fatalf("status = %d, want 403", rr.Code)
	}
	if gotPath != "/settings" || gotSource != "ui" {
		t.Fatalf("path=%q source=%q", gotPath, gotSource)
	}
	if !strings.Contains(gotBody, "builtinGuards") {
		t.Fatalf("body not preserved: %q", gotBody)
	}
	if !strings.Contains(rr.Body.String(), "immutable") {
		t.Fatalf("backend body not preserved: %q", rr.Body.String())
	}
}

func TestProxyUnknownAgent(t *testing.T) {
	reg := Registry{"baseline": {ID: "baseline", BaseURL: "http://127.0.0.1:1"}}
	rr := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodGet, "/api/agents/missing/status", nil)
	NewServer(reg, nil).Mux().ServeHTTP(rr, req)
	if rr.Code != http.StatusNotFound {
		t.Fatalf("status = %d, want 404", rr.Code)
	}
}

func TestUIChatKeyboardAndTranscript(t *testing.T) {
	reg := Registry{"baseline": {ID: "baseline", BaseURL: "http://agent:8080"}}
	rr := httptest.NewRecorder()
	NewServer(reg, nil).Mux().ServeHTTP(rr, httptest.NewRequest(http.MethodGet, "/", nil))
	body := rr.Body.String()
	for _, want := range []string{
		"e.key === 'Enter' && !e.shiftKey",
		"You:\\n",
		"Agent:\\n",
		"const answer = j.reply || j.error",
		"restoreDbBtn",
		"/api/db/restore",
	} {
		if !strings.Contains(body, want) {
			t.Fatalf("UI missing %q", want)
		}
	}
}

func TestDBRestoreProxy(t *testing.T) {
	var gotMethod, gotPath, gotHeader string
	backend := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotMethod = r.Method
		gotPath = r.URL.Path
		gotHeader = r.Header.Get("X-DB-Restore")
		w.Header().Set("Content-Type", "text/plain; charset=utf-8")
		_, _ = w.Write([]byte("restored: /data/db.sqlite\n"))
	}))
	defer backend.Close()

	srv := NewServer(Registry{"baseline": {ID: "baseline", BaseURL: "http://agent:8080"}}, nil)
	srv.dbAdminURL = backend.URL
	h := srv.Mux()

	rr := httptest.NewRecorder()
	h.ServeHTTP(rr, httptest.NewRequest(http.MethodPost, "/api/db/restore", nil))
	if rr.Code != http.StatusOK || !strings.Contains(rr.Body.String(), "restored") {
		t.Fatalf("restore status=%d body=%q", rr.Code, rr.Body.String())
	}
	if gotMethod != http.MethodPost || gotPath != "/restore" || gotHeader != "ui" {
		t.Fatalf("method=%q path=%q header=%q", gotMethod, gotPath, gotHeader)
	}

	rr = httptest.NewRecorder()
	h.ServeHTTP(rr, httptest.NewRequest(http.MethodGet, "/api/db/restore", nil))
	if rr.Code != http.StatusMethodNotAllowed {
		t.Fatalf("GET status = %d, want 405", rr.Code)
	}
}

func TestAugustusAPIAndReport(t *testing.T) {
	reports := t.TempDir()
	runID := "20261023T120000Z"
	runDir := filepath.Join(reports, runID)
	augustusDir := filepath.Join(runDir, "augustus")
	if err := os.MkdirAll(augustusDir, 0o755); err != nil {
		t.Fatal(err)
	}
	manifest := `{"preset":1,"runs":1,"target":"http://agent:8080",` +
		`"started_at":"2026-10-23T12:00:00Z","finished_at":"2026-10-23T12:01:00Z",` +
		`"augustus":{"status":"completed","target":"http://agent:8080",` +
		`"returncode":0,"probes":["dan.DAN_Jailbreak"],"db_changed":false}}`
	files := map[string]string{
		filepath.Join(runDir, "manifest.json"):    manifest,
		filepath.Join(augustusDir, "config.yaml"): "generators:\n  rest.Rest:\n    uri: http://agent:8080/chat\n",
		filepath.Join(augustusDir, "report.html"): "<h1>Augustus Scan Report</h1>",
	}
	for path, body := range files {
		if err := os.WriteFile(path, []byte(body), 0o644); err != nil {
			t.Fatal(err)
		}
	}

	srv := NewServer(Registry{"baseline": {ID: "baseline", BaseURL: "http://agent:8080"}}, nil)
	srv.reportsDir = reports
	h := srv.Mux()

	rr := httptest.NewRecorder()
	h.ServeHTTP(rr, httptest.NewRequest(http.MethodGet, "/api/augustus/runs", nil))
	if rr.Code != http.StatusOK {
		t.Fatalf("runs status = %d", rr.Code)
	}
	var runs []AugustusRun
	if err := json.Unmarshal(rr.Body.Bytes(), &runs); err != nil {
		t.Fatal(err)
	}
	if len(runs) != 1 || runs[0].RunID != runID || !runs[0].ReportAvailable {
		t.Fatalf("unexpected runs: %+v", runs)
	}
	if runs[0].Target != "http://agent:8080" || runs[0].Status != "completed" {
		t.Fatalf("manifest not mapped: %+v", runs[0])
	}

	for name, want := range map[string]string{
		"report.html": "Augustus Scan Report",
		"config.yaml": "rest.Rest",
	} {
		rr = httptest.NewRecorder()
		h.ServeHTTP(rr, httptest.NewRequest(http.MethodGet,
			"/api/augustus/runs/"+runID+"/"+name, nil))
		if rr.Code != http.StatusOK || !strings.Contains(rr.Body.String(), want) {
			t.Fatalf("%s: status=%d body=%q", name, rr.Code, rr.Body.String())
		}
	}

	rr = httptest.NewRecorder()
	h.ServeHTTP(rr, httptest.NewRequest(http.MethodGet,
		"/api/augustus/runs/missing/report.html", nil))
	if rr.Code != http.StatusNotFound {
		t.Fatalf("missing run status = %d", rr.Code)
	}
}

func TestAugustusUIPage(t *testing.T) {
	rr := httptest.NewRecorder()
	NewServer(Registry{"baseline": {ID: "baseline", BaseURL: "http://agent:8080"}}, nil).
		Mux().ServeHTTP(rr, httptest.NewRequest(http.MethodGet, "/augustus", nil))
	body := rr.Body.String()
	for _, want := range []string{"Augustus", "runSelect", "Effective config", "HTML report"} {
		if !strings.Contains(body, want) {
			t.Fatalf("Augustus UI missing %q", want)
		}
	}
}

func TestUIAndHealth(t *testing.T) {
	reg := Registry{"baseline": {ID: "baseline", BaseURL: "http://agent:8080"}}
	h := NewServer(reg, nil).Mux()
	for _, path := range []string{"/", "/ui"} {
		rr := httptest.NewRecorder()
		h.ServeHTTP(rr, httptest.NewRequest(http.MethodGet, path, nil))
		if rr.Code != http.StatusOK || !strings.Contains(rr.Body.String(), "agentSelect") {
			t.Fatalf("%s: status=%d body=%q", path, rr.Code, rr.Body.String())
		}
	}
	rr := httptest.NewRecorder()
	h.ServeHTTP(rr, httptest.NewRequest(http.MethodGet, "/healthz", nil))
	if rr.Code != http.StatusOK || rr.Body.String() != "ok" {
		t.Fatalf("health: %d %q", rr.Code, rr.Body.String())
	}
}
