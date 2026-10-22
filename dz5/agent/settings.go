package main

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"time"
)

// RuntimeSettings is the mutable protection matrix of presets 1-4.
type RuntimeSettings struct {
	BuiltinGuards bool `json:"builtinGuards"`
	ExternalGuard bool `json:"externalGuard"`
	AllowWrites   bool `json:"allowWrites"`
}

// SettingsRequest uses pointers to distinguish "missing" from false.
type SettingsRequest struct {
	BuiltinGuards *bool `json:"builtinGuards"`
	ExternalGuard *bool `json:"externalGuard"`
	AllowWrites   *bool `json:"allowWrites"`
	ConfirmWrites bool  `json:"confirmWrites"`
}

// AuditEvent is one JSONL line written for an applied settings change.
type AuditEvent struct {
	TS     string          `json:"ts"`
	Event  string          `json:"event"`
	Source string          `json:"source"`
	Old    RuntimeSettings `json:"old"`
	New    RuntimeSettings `json:"new"`
	Preset int             `json:"preset"`
	Result string          `json:"result"`
}

// AuditLogger appends events to AUDIT_LOG. A short open/append/close keeps the
// file usable across container restarts and avoids holding an fd forever.
type AuditLogger struct {
	path string
	mu   sync.Mutex
}

// NewAuditLogger validates that path is writable and creates the parent dir.
func NewAuditLogger(path string) (*AuditLogger, error) {
	path = strings.TrimSpace(path)
	if path == "" {
		return nil, fmt.Errorf("audit log path is empty")
	}
	if dir := filepath.Dir(path); dir != "." {
		if err := os.MkdirAll(dir, 0o755); err != nil {
			return nil, fmt.Errorf("create audit dir %s: %w", dir, err)
		}
	}
	f, err := os.OpenFile(path, os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0o644)
	if err != nil {
		return nil, fmt.Errorf("open audit log %s: %w", path, err)
	}
	if err := f.Close(); err != nil {
		return nil, fmt.Errorf("close audit log %s: %w", path, err)
	}
	return &AuditLogger{path: path}, nil
}

// Log appends one JSONL event.
func (a *AuditLogger) Log(ev AuditEvent) error {
	if a == nil {
		return fmt.Errorf("audit logger is nil")
	}
	if ev.TS == "" {
		ev.TS = time.Now().UTC().Format(time.RFC3339)
	}
	line, err := json.Marshal(ev)
	if err != nil {
		return fmt.Errorf("encode audit event: %w", err)
	}
	a.mu.Lock()
	defer a.mu.Unlock()
	f, err := os.OpenFile(a.path, os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0o644)
	if err != nil {
		return fmt.Errorf("open audit log %s: %w", a.path, err)
	}
	if _, err := f.Write(append(line, '\n')); err != nil {
		_ = f.Close()
		return fmt.Errorf("write audit log %s: %w", a.path, err)
	}
	if err := f.Close(); err != nil {
		return fmt.Errorf("close audit log %s: %w", a.path, err)
	}
	return nil
}

func presetOf(s RuntimeSettings) int {
	switch {
	case s.BuiltinGuards && s.ExternalGuard:
		return 4
	case s.BuiltinGuards:
		return 2
	case s.ExternalGuard:
		return 3
	default:
		return 1
	}
}
