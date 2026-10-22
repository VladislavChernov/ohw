package agentcore

import (
	"encoding/json"
	"io"
	"log"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
)

func newTestServer(t *testing.T, toggles bool) (*Engine, string) {
	t.Helper()
	dir := t.TempDir()
	auditPath := filepath.Join(dir, "audit.jsonl")
	audit, err := NewAuditLogger(auditPath)
	if err != nil {
		t.Fatalf("audit logger: %v", err)
	}
	return &Engine{
		cfg: Config{
			AllowRuntimeToggles: toggles,
			AuditLog:            auditPath,
			MinimalPrompt:       "minimal",
			GuardedPrompt:       "guarded",
			WallBaseURL:         "http://wall:8000",
			LLMBaseURL:          "http://ollama:11434",
			AgentName:           "baseline",
		},
		settings: RuntimeSettings{},
		audit:    audit,
		log:      log.New(io.Discard, "", 0),
	}, auditPath
}

func postSettings(s *Engine, body string) *httptest.ResponseRecorder {
	req := httptest.NewRequest(http.MethodPost, "/settings", strings.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("X-Settings-Source", "ui")
	rr := httptest.NewRecorder()
	s.HandleSettings(rr, req)
	return rr
}

func TestPresetOf(t *testing.T) {
	cases := []struct {
		s    RuntimeSettings
		want int
	}{
		{RuntimeSettings{}, 1},
		{RuntimeSettings{BuiltinGuards: true}, 2},
		{RuntimeSettings{ExternalGuard: true}, 3},
		{RuntimeSettings{BuiltinGuards: true, ExternalGuard: true}, 4},
	}
	for _, tc := range cases {
		if got := PresetOf(tc.s); got != tc.want {
			t.Fatalf("PresetOf(%+v) = %d, want %d", tc.s, got, tc.want)
		}
	}
}

func TestSettingsForbiddenWhenTogglesOff(t *testing.T) {
	s, auditPath := newTestServer(t, false)
	rr := postSettings(s, `{"builtinGuards":true,"externalGuard":false,"allowWrites":false}`)
	if rr.Code != http.StatusForbidden {
		t.Fatalf("status = %d, want 403", rr.Code)
	}
	if got := PresetOf(s.Snapshot()); got != 1 {
		t.Fatalf("settings changed to preset %d", got)
	}
	data, _ := os.ReadFile(auditPath)
	lines := strings.Split(strings.TrimSpace(string(data)), "\n")
	if len(lines) != 1 {
		t.Fatalf("audit lines = %d, want 1: %q", len(lines), data)
	}
	var ev AuditEvent
	if err := json.Unmarshal([]byte(lines[0]), &ev); err != nil {
		t.Fatalf("decode audit: %v", err)
	}
	if !strings.Contains(ev.Result, "forbidden") || ev.Old != ev.New {
		t.Fatalf("unexpected refusal audit: %+v", ev)
	}
}

func TestSettingsValidation(t *testing.T) {
	cases := []string{
		`{`,
		`{"builtinGuards":true}`,
		`{"builtinGuards":"yes","externalGuard":false,"allowWrites":false}`,
		`{"builtinGuards":true,"externalGuard":false,"allowWrites":false,"unknown":true}`,
		`{"builtinGuards":false,"externalGuard":false,"allowWrites":true,"confirmWrites":false}`,
	}
	for _, body := range cases {
		s, auditPath := newTestServer(t, true)
		rr := postSettings(s, body)
		if rr.Code != http.StatusBadRequest {
			t.Fatalf("body %s: status = %d, want 400 (%s)", body, rr.Code, rr.Body.String())
		}
		if got := PresetOf(s.Snapshot()); got != 1 {
			t.Fatalf("body %s changed preset to %d", body, got)
		}
		data, _ := os.ReadFile(auditPath)
		lines := strings.Split(strings.TrimSpace(string(data)), "\n")
		if len(lines) != 1 {
			t.Fatalf("body %s: audit lines = %d, want 1: %q", body, len(lines), data)
		}
		var ev AuditEvent
		if err := json.Unmarshal([]byte(lines[0]), &ev); err != nil {
			t.Fatalf("body %s: decode audit: %v", body, err)
		}
		if !strings.HasPrefix(ev.Result, "rejected") || ev.Old != ev.New {
			t.Fatalf("body %s: unexpected refusal audit: %+v", body, ev)
		}
	}
}

func TestSettingsAppliesAndAudits(t *testing.T) {
	s, auditPath := newTestServer(t, true)
	rr := postSettings(s, `{"builtinGuards":true,"externalGuard":true,"allowWrites":false,"confirmWrites":false}`)
	if rr.Code != http.StatusOK {
		t.Fatalf("status = %d, want 200 (%s)", rr.Code, rr.Body.String())
	}
	snap := s.Snapshot()
	if !snap.BuiltinGuards || !snap.ExternalGuard || snap.AllowWrites {
		t.Fatalf("unexpected settings: %+v", snap)
	}
	if got := PresetOf(snap); got != 4 {
		t.Fatalf("preset = %d, want 4", got)
	}
	var status map[string]interface{}
	if err := json.Unmarshal(rr.Body.Bytes(), &status); err != nil {
		t.Fatalf("decode status: %v", err)
	}
	if status["preset"].(float64) != 4 {
		t.Fatalf("response preset = %v", status["preset"])
	}
	if status["runtimeToggles"] != true {
		t.Fatalf("runtimeToggles = %v", status["runtimeToggles"])
	}

	data, err := os.ReadFile(auditPath)
	if err != nil {
		t.Fatalf("read audit: %v", err)
	}
	lines := strings.Split(strings.TrimSpace(string(data)), "\n")
	if len(lines) != 1 {
		t.Fatalf("audit lines = %d, want 1: %q", len(lines), data)
	}
	var ev AuditEvent
	if err := json.Unmarshal([]byte(lines[0]), &ev); err != nil {
		t.Fatalf("decode audit: %v", err)
	}
	if ev.Event != "settings.update" || ev.Source != "ui" || ev.Preset != 4 || ev.Result != "ok" {
		t.Fatalf("unexpected audit event: %+v", ev)
	}
	if ev.Old != (RuntimeSettings{}) || ev.New != snap {
		t.Fatalf("audit old/new mismatch: %+v", ev)
	}
}

func TestStatusReflectsRuntimeSettings(t *testing.T) {
	s, _ := newTestServer(t, true)
	s.settings = RuntimeSettings{ExternalGuard: true}
	req := httptest.NewRequest(http.MethodGet, "/status", nil)
	rr := httptest.NewRecorder()
	s.HandleStatus(rr, req)
	var got map[string]interface{}
	if err := json.Unmarshal(rr.Body.Bytes(), &got); err != nil {
		t.Fatalf("decode: %v", err)
	}
	if got["preset"].(float64) != 3 || got["externalGuard"] != true {
		t.Fatalf("unexpected status: %v", got)
	}
}

func TestSettingsConcurrentStatus(t *testing.T) {
	s, _ := newTestServer(t, true)
	var wg sync.WaitGroup
	for i := 0; i < 20; i++ {
		wg.Add(2)
		go func() {
			defer wg.Done()
			req := httptest.NewRequest(http.MethodGet, "/status", nil)
			s.HandleStatus(httptest.NewRecorder(), req)
		}()
		go func() {
			defer wg.Done()
			postSettings(s, `{"builtinGuards":true,"externalGuard":false,"allowWrites":false}`)
		}()
	}
	wg.Wait()
}

func TestFixedSettingsImmutable(t *testing.T) {
	s, _ := newTestServer(t, true)
	s.cfg.Fixed = true
	s.cfg.AgentName = "fixed"
	s.mode = Mode{AgentName: "fixed", Fixed: true, InitialSettings: RuntimeSettings{BuiltinGuards: true}}
	s.settings = RuntimeSettings{BuiltinGuards: true}
	rr := postSettings(s, `{"builtinGuards":false,"externalGuard":false,"allowWrites":false}`)
	if rr.Code != http.StatusForbidden {
		t.Fatalf("status = %d, want 403", rr.Code)
	}
	if got := s.Snapshot(); got != (RuntimeSettings{BuiltinGuards: true}) {
		t.Fatalf("fixed settings changed: %+v", got)
	}
}
