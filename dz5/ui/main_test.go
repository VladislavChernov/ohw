package main

import (
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
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
	} {
		if !strings.Contains(body, want) {
			t.Fatalf("UI missing %q", want)
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
